package dev.winnie.app.ui

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import dev.winnie.app.R

/**
 * Proves the Chinese resources actually load, not just that they exist. Runs
 * with a `zh` qualifier so Android picks values-zh/strings.xml.
 */
@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "zh")
class ChineseStringsTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    @Test
    fun resolvesChineseStrings() {
        assertEquals("登录", context.getString(R.string.login_sign_in))
        assertEquals("聊天", context.getString(R.string.tab_chat))
        assertEquals("手机数据", context.getString(R.string.phone_title))
        assertEquals("问 Winnie 任何事", context.getString(R.string.chat_answer_anything))
    }

    @Test
    fun resolvesChinesePlurals() {
        // Chinese has one plural form; the count is still substituted.
        assertEquals(
            "已同步 3 条记录。",
            context.resources.getQuantityString(R.plurals.phone_synced_records, 3, 3),
        )
    }
}
