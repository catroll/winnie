package dev.winnie.app.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import kotlin.concurrent.thread
import kotlin.math.max

/** Reports a detected wake word, and a failure that stops detection for good. */
interface WakeWordDetector {
    fun start(onWake: (String) -> Unit, onError: (Throwable) -> Unit = {})
    fun stop()

    /** Frees the native model; [start] creates a new one. */
    fun release()
}

/**
 * On-device keyword spotting with sherpa-onnx.
 *
 * The model is the open-vocabulary Chinese/English zipformer: the keyword set
 * lives in `assets/kws/keywords.txt`, so "winnie" is a data file, not a
 * retrained network. Everything runs on the phone — no audio leaves the device
 * until a wake is followed by an actual voice turn.
 *
 * The detector owns the microphone while it runs. Android gives no way for a
 * third-party app to listen on a low-power DSP, so this is a normal
 * `AudioRecord`: a foreground service must hold it, the privacy indicator stays
 * on, and another capture app may receive silence for as long as it runs.
 *
 * One worker thread reads 100 ms blocks, appends each to [ring] and feeds the
 * model. A detection resets the decoder instead of tearing anything down, so
 * the next wake works without reloading the model.
 */
class SherpaWakeWordDetector(
    context: Context,
    private val ring: PcmRingBuffer,
) : WakeWordDetector {

    private val appContext = context.applicationContext

    @Volatile
    private var running = false

    private var worker: Thread? = null

    @Volatile
    private var spotter: KeywordSpotter? = null

    override fun start(onWake: (String) -> Unit, onError: (Throwable) -> Unit) {
        if (running) return
        running = true
        worker = thread(name = "winnie-wake", isDaemon = true) {
            loop(onWake, onError)
        }
    }

    override fun stop() {
        running = false
        // The read call blocks for at most one 100 ms block, so a short join is
        // enough and keeps the microphone ours until it really is released.
        worker?.join(1_000)
        worker = null
        spotter?.release()
        spotter = null
    }

    override fun release() = stop()

    @SuppressLint("MissingPermission") // The service checks RECORD_AUDIO first.
    private fun loop(onWake: (String) -> Unit, onError: (Throwable) -> Unit) {
        try {
            val engine = spotter ?: KeywordSpotter(appContext.assets, config()).also {
                spotter = it
            }
            val stream = engine.createStream()
            val record = openMicrophone() ?: throw IllegalStateException(
                "The microphone is not available for wake detection"
            )
            try {
                record.startRecording()
                if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
                    throw IllegalStateException("The microphone could not start recording")
                }
                val samples = ShortArray(FRAME_SAMPLES)
                val bytes = ByteArray(FRAME_SAMPLES * 2)
                while (running) {
                    val read = record.read(samples, 0, samples.size)
                    if (read <= 0) {
                        if (read < 0) throw IllegalStateException("AudioRecord.read failed with $read")
                        continue
                    }
                    writeBytes(samples, read, bytes)
                    ring.append(bytes, read * 2)
                    stream.acceptWaveform(
                        FloatArray(read) { samples[it] / 32768.0f },
                        sampleRate = SAMPLE_RATE,
                    )
                    while (running && engine.isReady(stream)) {
                        engine.decode(stream)
                        val keyword = engine.getResult(stream).keyword
                        if (keyword.isBlank()) continue
                        // Reset immediately: a second detection while the owner
                        // is still speaking would launch a second turn.
                        engine.reset(stream)
                        onWake(keyword)
                    }
                }
            } finally {
                runCatching {
                    if (record.recordingState == AudioRecord.RECORDSTATE_RECORDING) record.stop()
                }
                record.release()
            }
        } catch (e: Throwable) {
            if (running) {
                Log.e(TAG, "wake word detection stopped", e)
                onError(e)
            }
        } finally {
            running = false
        }
    }

    private fun openMicrophone(): AudioRecord? {
        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) return null
        val bufferSize = max(minBuffer * 2, FRAME_SAMPLES * 2 * 4)
        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return null
        }
        return record
    }

    private fun writeBytes(samples: ShortArray, count: Int, out: ByteArray) {
        for (index in 0 until count) {
            val value = samples[index].toInt()
            out[index * 2] = (value and 0xFF).toByte()
            out[index * 2 + 1] = ((value shr 8) and 0xFF).toByte()
        }
    }

    private fun config(): KeywordSpotterConfig {
        val dir = "kws"
        return KeywordSpotterConfig(
            featConfig = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = FEATURE_DIM),
            modelConfig = OnlineModelConfig(
                transducer = OnlineTransducerModelConfig(
                    encoder = "$dir/encoder.int8.onnx",
                    decoder = "$dir/decoder.onnx",
                    joiner = "$dir/joiner.int8.onnx",
                ),
                tokens = "$dir/tokens.txt",
                numThreads = 2,
                debug = false,
                provider = "cpu",
            ),
            keywordsFile = "$dir/keywords.txt",
            keywordsScore = KEYWORD_BOOST,
            keywordsThreshold = KEYWORD_THRESHOLD,
            // Zero blanks: a wake word is usually followed immediately by the
            // command ("winnie, what is the weather"). With the default of two
            // trailing blank frames the trigger waits for a pause that a
            // continuous speaker never makes, and the measured real recording
            // "Winnie the Pooh" only fires at zero. See docs/voice-wake.md.
            numTrailingBlanks = 0,
            maxActivePaths = 4,
        )
    }

    private companion object {
        const val TAG = "WinnieWake"
        const val SAMPLE_RATE = 16_000
        const val FEATURE_DIM = 80
        const val FRAME_MS = 100
        const val FRAME_SAMPLES = SAMPLE_RATE / 1000 * FRAME_MS

        // Generous on purpose: a missed wake is worse than an occasional extra
        // chime, and the chime has a cancel button. Measured false accepts on a
        // real phone decide whether these move in docs/voice-wake.md.
        const val KEYWORD_BOOST = 1.5f
        const val KEYWORD_THRESHOLD = 0.25f
    }
}
