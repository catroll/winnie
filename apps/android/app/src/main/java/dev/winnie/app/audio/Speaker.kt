package dev.winnie.app.audio

import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * What the chat needs from a speech engine: read this reply, stop, and say
 * whether something is being said.
 *
 * It is an interface so the chat can be exercised without a device and so a
 * second engine (a supplier's synthesised audio, played as a stream) can be
 * added without the chat knowing which one it holds.
 */
interface Speaker {

    /** True from the first utterance until the last one finishes or is stopped. */
    val speaking: StateFlow<Boolean>

    /**
     * A reason the engine could not speak, already phrased for the owner. It is
     * a flow rather than a property because the same failure twice must be shown
     * twice.
     */
    val failures: SharedFlow<String>

    /** Reads [markdown] aloud, replacing whatever is being said. */
    fun speak(markdown: String)

    fun stop()

    /** Releases the engine. The speaker must not be used afterwards. */
    fun release()
}
