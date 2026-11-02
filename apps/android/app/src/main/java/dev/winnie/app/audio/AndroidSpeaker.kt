package dev.winnie.app.audio

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import dev.winnie.app.AppStrings
import dev.winnie.app.R
import dev.winnie.app.data.AppSettings
import java.util.Locale
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Reads replies with the phone's own text-to-speech engine
 * (`android.speech.tts.TextToSpeech`, part of the platform since API 1).
 *
 * No vendor and no network: the engine ships with the device (Google TTS,
 * Samsung TTS, or the vendor's), works offline, and costs nothing per reply.
 * Which engine and which voice it uses is the owner's choice in the system's
 * accessibility settings, so the app does not reimplement that picker — it
 * honours the selected voice, and only overrides it when the settings screen has
 * pinned one by name.
 *
 * The engine is created on the first utterance rather than in the constructor:
 * it binds to a service, and an app that is opened to read a message should not
 * pay for a synthesiser it may never use.
 */
class AndroidSpeaker(
    context: Context,
    private val settings: AppSettings,
) : Speaker {

    private val appContext = context.applicationContext

    // Nothing here needs a main thread: the engine posts to its own handler and
    // the flows are safe to update from any thread.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _speaking = MutableStateFlow(false)
    override val speaking: StateFlow<Boolean> = _speaking.asStateFlow()

    private val _failures = MutableSharedFlow<String>(extraBufferCapacity = 4)
    override val failures: SharedFlow<String> = _failures.asSharedFlow()

    private val lock = Any()
    private var engine: TextToSpeech? = null
    private var ready: CompletableDeferred<Boolean>? = null

    /**
     * Bumped by every [speak] and [stop]. An utterance whose token is stale is
     * ignored: the engine is asynchronous, so a reply that is still waiting for
     * the engine to initialise must not start after the owner pressed stop.
     */
    private var generation = 0
    private var pending = 0
    private var released = false

    override fun speak(markdown: String) {
        val text = SpeakableText.reduce(markdown)
        if (text.isBlank()) {
            return
        }
        val token = synchronized(lock) {
            generation += 1
            generation
        }
        scope.launch {
            if (!awaitEngine()) {
                fail()
                return@launch
            }
            val current = synchronized(lock) { token == generation }
            if (!current) {
                return@launch
            }
            start(token, text)
        }
    }

    /**
     * Waits for the engine to report success. A device with no engine at all
     * answers with an error status, and one that never answers must not pin the
     * caller, so both become "cannot speak" after a bounded wait.
     */
    private suspend fun awaitEngine(): Boolean {
        val deferred = synchronized(lock) {
            if (released) {
                return false
            }
            ready ?: CompletableDeferred<Boolean>().also { created ->
                ready = created
                engine = createEngine(created)
            }
        }
        return withTimeoutOrNull(ENGINE_TIMEOUT_MS) { deferred.await() } ?: false
    }

    private fun createEngine(deferred: CompletableDeferred<Boolean>): TextToSpeech? =
        try {
            TextToSpeech(appContext) { status ->
                deferred.complete(status == TextToSpeech.SUCCESS)
            }.also { it.setOnUtteranceProgressListener(progress) }
        } catch (e: Exception) {
            deferred.complete(false)
            null
        }

    private fun start(token: Int, text: String) {
        val tts = synchronized(lock) { engine }
        if (tts == null) {
            fail()
            return
        }
        val output = settings.voiceOutput
        val chunks = SpeakableText.chunk(text)
        val locale = Locale.forLanguageTag(SpeakableText.languageTag(text))
        val pinned = output.voice.takeIf { it.isNotBlank() }?.let { name ->
            runCatching { tts.voices?.firstOrNull { it.name == name } }.getOrNull()
        }
        val languageStatus = if (pinned != null) {
            runCatching { tts.voice = pinned }
            TextToSpeech.LANG_AVAILABLE
        } else {
            runCatching { tts.setLanguage(locale) }
                .getOrDefault(TextToSpeech.LANG_NOT_SUPPORTED)
        }
        if (
            languageStatus == TextToSpeech.LANG_MISSING_DATA ||
            languageStatus == TextToSpeech.LANG_NOT_SUPPORTED
        ) {
            fail()
            return
        }
        runCatching { tts.setSpeechRate(output.rate) }
        synchronized(lock) { pending = chunks.size }
        _speaking.value = true
        chunks.forEachIndexed { index, chunk ->
            val queued = runCatching {
                tts.speak(
                    chunk,
                    if (index == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD,
                    null,
                    utteranceId(token, index),
                )
            }.getOrDefault(TextToSpeech.ERROR)
            if (queued == TextToSpeech.ERROR) {
                finishOne(token)
            }
        }
    }

    private val progress = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) = Unit

        override fun onDone(utteranceId: String?) = finish(utteranceId)

        override fun onStop(utteranceId: String?, interrupted: Boolean) = finish(utteranceId)

        @Suppress("OVERRIDE_DEPRECATION")
        override fun onError(utteranceId: String?) = finish(utteranceId)
    }

    /** Ignores the callback of an utterance that a newer [speak] or [stop] replaced. */
    private fun finish(utteranceId: String?) {
        val token = utteranceId
            ?.removePrefix(UTTERANCE_PREFIX)
            ?.substringBefore('-')
            ?.toIntOrNull()
            ?: return
        finishOne(token)
    }

    private fun finishOne(token: Int) {
        val last = synchronized(lock) {
            if (token != generation || pending <= 0) {
                return
            }
            pending -= 1
            pending == 0
        }
        if (last) {
            _speaking.value = false
        }
    }

    private fun fail() {
        _speaking.value = false
        _failures.tryEmit(AppStrings.get(R.string.voice_output_unavailable))
    }

    override fun stop() {
        synchronized(lock) {
            generation += 1
            pending = 0
        }
        _speaking.value = false
        synchronized(lock) { engine }?.stop()
    }

    override fun release() {
        val tts = synchronized(lock) {
            if (released) {
                return
            }
            released = true
            generation += 1
            pending = 0
            ready = null
            engine.also { engine = null }
        }
        _speaking.value = false
        runCatching { tts?.stop() }
        runCatching { tts?.shutdown() }
        scope.cancel()
    }

    private fun utteranceId(token: Int, index: Int): String =
        "$UTTERANCE_PREFIX$token-$index"

    private companion object {
        const val UTTERANCE_PREFIX = "winnie-"

        /** A device whose engine never answers is not one that can read aloud. */
        const val ENGINE_TIMEOUT_MS = 5_000L
    }
}
