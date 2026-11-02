package dev.winnie.app.audio

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The reduction decides what the owner hears, so it is tested as text rather
 * than through an engine: a heading marker read aloud is a bug the device cannot
 * report, and the console has the same rules.
 */
class SpeakableTextTest {

    @Test
    fun markdownMarkersAreNotSpoken() {
        val reduced = SpeakableText.reduce(
            """
                ## Weather

                It is **sunny**, with a high of `21C`.

                - Bring a jacket
                - Take the long way
            """.trimIndent(),
        )

        assertEquals("Weather It is sunny, with a high of 21C. Bring a jacket Take the long way", reduced)
    }

    @Test
    fun aCodeBlockIsDroppedEntirely() {
        val reduced = SpeakableText.reduce(
            "Run this:\n\n```bash\ngit push --force\n```\n\nThen tell me.",
        )

        assertEquals("Run this: Then tell me.", reduced)
    }

    @Test
    fun aLinkKeepsItsLabelAndLosesItsAddress() {
        val reduced = SpeakableText.reduce(
            "See [the schedule](https://example.com/schedule?a=1&b=2) now.",
        )

        assertEquals("See the schedule now.", reduced)
    }

    @Test
    fun aTableBecomesWordsWithoutPipes() {
        val reduced = SpeakableText.reduce(
            """
                | Time | What |
                | --- | --- |
                | 09:00 | Standup |
            """.trimIndent(),
        )

        assertTrue(reduced, !reduced.contains('|'))
        assertTrue(reduced, reduced.contains("09:00"))
        assertTrue(reduced, reduced.contains("Standup"))
    }

    @Test
    fun chineseSurvivesTheReduction() {
        assertEquals(
            "今天武汉多云，最高 21 度。",
            SpeakableText.reduce("今天武汉**多云**，最高 `21` 度。"),
        )
    }

    @Test
    fun aLongReplyIsSplitAtSentenceEnds() {
        val reply = (1..20).joinToString("") { "这是第${it}句话，用来测试分句是否在句号处结束。" }

        val chunks = SpeakableText.chunk(SpeakableText.reduce(reply))

        assertTrue(chunks.size > 1)
        assertTrue(chunks.all { it.length <= SpeakableText.MAX_CHUNK_CHARS })
        // No text is lost between the chunks.
        assertEquals(SpeakableText.reduce(reply).replace(" ", ""), chunks.joinToString("").replace(" ", ""))
    }

    @Test
    fun anUnpunctuatedParagraphStillRespectsTheEngineLimit() {
        val chunks = SpeakableText.chunk("a".repeat(500))

        assertEquals(3, chunks.size)
        assertTrue(chunks.all { it.length <= SpeakableText.MAX_CHUNK_CHARS })
    }

    @Test
    fun theReplyScriptDecidesTheVoiceLanguage() {
        assertEquals("zh-CN", SpeakableText.languageTag("今天天气怎么样？"))
        assertEquals("en-US", SpeakableText.languageTag("How is the weather today?"))
        // A Chinese sentence with an English word in it is still Chinese: the
        // majority script wins, not the first character.
        assertEquals("zh-CN", SpeakableText.languageTag("武汉今天的天气怎么样 today"))
        assertEquals("ja-JP", SpeakableText.languageTag("今日はいい天気ですね"))
        assertEquals("ko-KR", SpeakableText.languageTag("오늘 날씨 좋네요"))
    }
}
