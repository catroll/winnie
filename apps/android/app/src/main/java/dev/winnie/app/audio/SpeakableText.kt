package dev.winnie.app.audio

/**
 * A Markdown reply reduced to what can be read aloud.
 *
 * The assistant answers in Markdown, which is written to be read: a fenced code
 * block, a link's URL or a table's pipes would be spoken as punctuation. This is
 * the same reduction the console applies (`apps/admin/src/lib/voice/speech.ts`),
 * so a reply sounds the same in both clients — keep the two in step when one
 * changes.
 */
object SpeakableText {

    /** Past this the reply is a document; reading all of it wastes the engine. */
    internal const val MAX_CHARS = 6_000

    /**
     * A speech engine wants sentences, not one long string: Android's own
     * limit is 4,000 characters per utterance, and a short queue also means
     * stop takes effect between sentences.
     */
    internal const val MAX_CHUNK_CHARS = 220
    private const val MIN_SENTENCE_CHARS = 30

    private val FENCED_CODE = Regex("```[\\s\\S]*?```")
    private val FENCED_CODE_TILDE = Regex("~~~[\\s\\S]*?~~~")
    private val IMAGE = Regex("!\\[[^\\]]*\\]\\([^)]*\\)")
    private val LINK = Regex("\\[([^\\]]+)\\]\\([^)]*\\)")
    private val TAG = Regex("<[^>]*>")
    private val HEADING = Regex("(?m)^\\s{0,3}(?:#{1,6}|>)\\s?")
    private val LIST_MARKER = Regex("(?m)^\\s*(?:[-*+]|\\d+[.)])\\s+")
    private val TABLE_RULE = Regex("(?m)^\\s*\\|?[\\s:|-]*\\|[\\s:|-]*\\$")
    private val PIPE = Regex("\\|")
    private val URL = Regex("https?://\\S+")
    private val EMPHASIS = Regex("(\\*\\*|__|\\*|_|~~|`)")
    private val WHITESPACE = Regex("\\s+")

    /**
     * Strip the parts of a Markdown reply that exist only for the eye.
     *
     * Fenced code is dropped rather than flattened: reading a block of code
     * aloud is noise and its punctuation becomes words.
     */
    fun reduce(markdown: String): String {
        var text = markdown
        text = FENCED_CODE.replace(text, " ")
        text = FENCED_CODE_TILDE.replace(text, " ")
        text = IMAGE.replace(text, " ")
        text = LINK.replace(text, "$1")
        text = TAG.replace(text, " ")
        text = HEADING.replace(text, "")
        text = LIST_MARKER.replace(text, "")
        text = TABLE_RULE.replace(text, " ")
        text = PIPE.replace(text, " ")
        text = URL.replace(text, " ")
        text = EMPHASIS.replace(text, "")
        text = WHITESPACE.replace(text, " ").trim()
        return text.take(MAX_CHARS)
    }

    /**
     * Split one reduced reply into utterances at sentence ends.
     *
     * A chunk ends at a sentence terminator once it is long enough to sound
     * like a sentence, and at [MAX_CHUNK_CHARS] regardless, so one unpunctuated
     * paragraph cannot exceed the engine's limit.
     */
    fun chunk(text: String): List<String> {
        val chunks = mutableListOf<String>()
        val current = StringBuilder()
        for (char in text) {
            current.append(char)
            val sentenceEnd = "。！？!?；;".indexOf(char) >= 0
            if (
                (sentenceEnd && current.length >= MIN_SENTENCE_CHARS) ||
                current.length >= MAX_CHUNK_CHARS
            ) {
                val chunk = current.toString().trim()
                if (chunk.isNotEmpty()) {
                    chunks += chunk
                }
                current.setLength(0)
            }
        }
        val tail = current.toString().trim()
        if (tail.isNotEmpty()) {
            chunks += tail
        }
        return chunks
    }

    /**
     * Pick the language from the reply's script, not the app's UI language: the
     * owner writes Chinese and English in the same conversation, so each reply
     * decides for itself which voice can pronounce it.
     */
    fun languageTag(text: String): String {
        var cjk = 0
        var latin = 0
        var kana = 0
        var hangul = 0
        for (char in text) {
            val code = char.code
            when {
                code in 0x3040..0x30ff || code in 0x31f0..0x31ff -> kana += 1
                code in 0xac00..0xd7af -> hangul += 1
                code in 0x3400..0x9fff -> cjk += 1
                code in 0x41..0x5a || code in 0x61..0x7a -> latin += 1
            }
        }
        return when {
            kana > cjk + latin -> "ja-JP"
            hangul > cjk + latin -> "ko-KR"
            cjk > latin -> "zh-CN"
            else -> "en-US"
        }
    }
}
