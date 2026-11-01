package dev.winnie.app.ui

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.w3c.dom.Element
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Chinese and English must stay in step.
 *
 * Android silently falls back to English for a missing key, so a translation
 * gap does not crash — it just ships a half-Chinese screen. Comparing the two
 * files is the only check that catches it without a device: the resource merger
 * will not tell you.
 */
class StringResourceParityTest {

    private val base = File("src/main/res/values/strings.xml")
    private val chinese = File("src/main/res/values-zh/strings.xml")

    @Test
    fun bothResourceFilesExist() {
        assertTrue("missing ${base.path}", base.isFile)
        assertTrue("missing ${chinese.path}", chinese.isFile)
    }

    @Test
    fun chineseDefinesEveryEnglishString() {
        assertEquals(names(base, "string"), names(chinese, "string"))
    }

    @Test
    fun chineseDefinesEveryEnglishPlural() {
        assertEquals(names(base, "plurals"), names(chinese, "plurals"))
    }

    @Test
    fun bothLocalesDefineTheSameFormatArguments() {
        // A translation that drops %1$s makes the formatted call crash at
        // runtime, so compare the placeholders per key rather than only names.
        assertEquals(arguments(base, "string"), arguments(chinese, "string"))
    }

    private fun names(file: File, tag: String): Set<String> =
        elements(file, tag).map { it.getAttribute("name") }.toSet()

    private fun arguments(file: File, tag: String): Map<String, List<String>> =
        elements(file, tag).associate { element ->
            val text = element.textContent
            val placeholders = Regex("""%\d+\$[a-zA-Z]""").findAll(text)
                .map { it.value }
                .sorted()
                .toList()
            element.getAttribute("name") to placeholders
        }

    private fun elements(file: File, tag: String): List<Element> {
        val nodes = DocumentBuilderFactory.newInstance()
            .newDocumentBuilder()
            .parse(file)
            .getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }
}
