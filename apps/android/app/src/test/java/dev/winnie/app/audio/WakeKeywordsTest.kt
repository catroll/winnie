package dev.winnie.app.audio

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Guards the shipped keyword file against the failure that is invisible at
 * runtime: a token the model does not know.
 *
 * The spotter compares the model's decoded phoneme sequence with the tokens in
 * this file. A misspelled token does not throw — it simply never matches, so the
 * wake word stops working and every layer above it looks healthy. Checking each
 * token against `tokens.txt` turns that into a test failure.
 */
class WakeKeywordsTest {

    private val dir = File("src/main/assets/kws")
    private val keywords = File(dir, "keywords.txt")
    private val tokens = File(dir, "tokens.txt")

    private fun tokenSet(): Set<String> =
        tokens.readLines()
            .mapNotNull { it.trim().split(" ").firstOrNull()?.takeIf(String::isNotBlank) }
            .toSet()

    private fun entries(): List<List<String>> =
        keywords.readLines()
            .map(String::trim)
            .filter { it.isNotEmpty() && !it.startsWith("#") }
            .map { it.split(" ") }

    @Test
    fun theFilesExist() {
        assertTrue("missing ${keywords.path}", keywords.isFile)
        assertTrue("missing ${tokens.path}", tokens.isFile)
    }

    @Test
    fun everyKeywordTokenIsInTheModelVocabulary() {
        val known = tokenSet()
        val unknown = entries().flatMap { line ->
            line.filterNot { it.startsWith("@") || it.startsWith(":") || it.startsWith("#") }
                .filterNot(known::contains)
        }
        assertEquals("tokens the model cannot decode: $unknown", emptyList<String>(), unknown)
    }

    @Test
    fun everyKeywordCarriesADisplayLabel() {
        val missing = entries().filterNot { line -> line.any { it.startsWith("@") } }
        assertEquals("lines without an @label: $missing", emptyList<List<String>>(), missing)
    }

    @Test
    fun theLabelsHaveNoSpaces() {
        // The keyword file is space separated, so a label with a space splits
        // into two items and the rest of the line is parsed as tokens.
        val bad = entries().flatMap { line ->
            line.filter { it.startsWith("@") && it.contains(' ') }
        }
        assertEquals("labels with spaces: $bad", emptyList<String>(), bad)
    }

    @Test
    fun winnieHasMoreThanOnePronunciation() {
        // One measured voice decoded WINNIE with EH1 rather than the dictionary
        // IH1, which is why the file holds variants; see docs/voice-wake.md.
        val variants = entries().count { line -> line.lastOrNull() == "@winnie" }
        assertTrue("expected several pronunciations, found $variants", variants >= 2)
    }
}
