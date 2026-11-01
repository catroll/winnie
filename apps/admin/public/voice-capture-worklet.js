/**
 * AudioWorklet processor for the console's voice recorder.
 *
 * It runs on the audio rendering thread and does one thing: hand every block of
 * captured samples to the page. Endpointing, level metering and the WAV encode
 * stay on the main thread because they are bookkeeping, not audio work, and a
 * worklet cannot touch the DOM.
 *
 * The node is created with `channelCount: 1` and `channelCountMode: "explicit"`,
 * so the mixer has already downmixed a stereo microphone to one channel here.
 */

class WinnieCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input.length > 0 && input[0].length > 0) {
      // Copy before posting: the rendering thread reuses these buffers.
      const mono = input[0].slice(0);
      this.port.postMessage(mono, [mono.buffer]);
    }
    // Keep the processor alive while the turn runs; the page disconnects it.
    return true;
  }
}

registerProcessor('winnie-capture', WinnieCaptureProcessor);
