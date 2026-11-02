package dev.winnie.app.data

/**
 * How replies are read aloud.
 *
 * [provider] names the engine family and [voice] an id inside it, because a
 * voice name is not unique across engines. `system` is the phone's own
 * text-to-speech service, whose engine and voices differ per device; a
 * supplier's synthesised audio, if one is ever added, would be a server-side
 * catalogue under its own provider. A choice saved for one family must never be
 * handed to another, so a saved voice the current engine does not report falls
 * back to the engine's own default instead of going silent.
 */
data class VoiceOutput(
    val enabled: Boolean = true,
    val provider: String = SYSTEM,
    /** Engine-scoped voice name; blank means the engine's own default. */
    val voice: String = "",
    val rate: Float = 1f,
) {
    /** A rate outside what an engine accepts is a settings bug, not a voice. */
    fun normalized(): VoiceOutput = copy(rate = rate.coerceIn(MIN_RATE, MAX_RATE))

    companion object {
        const val SYSTEM = "system"

        /** Android's `TextToSpeech.setSpeechRate` accepts 0.1–10 but 0.5–2 is speech. */
        const val MIN_RATE = 0.5f
        const val MAX_RATE = 2f

        /** The four rates the account screen offers, the same as the console. */
        val RATES = listOf(0.8f, 1f, 1.2f, 1.5f)
    }
}
