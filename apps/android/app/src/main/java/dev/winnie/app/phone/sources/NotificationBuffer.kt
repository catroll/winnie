package dev.winnie.app.phone.sources

import android.content.Context
import dev.winnie.app.data.PhoneNotificationItem
import dev.winnie.app.data.WinnieApi
import java.io.File
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString

/**
 * A small append-only queue of notifications captured since the last upload.
 *
 * The listener service may be killed at any time, so captured notifications go
 * to a file instead of memory. `ack` drops the entries the server accepted and
 * rewrites the file atomically; a crash between upload and ack re-sends a few
 * items, which the server deduplicates by external id.
 */
class NotificationBuffer(context: Context) {

    private val file = File(context.filesDir, "phone/notifications.jsonl")
    private val lock = Any()

    fun append(item: PhoneNotificationItem) {
        synchronized(lock) {
            file.parentFile?.mkdirs()
            file.appendText(WinnieApi.json.encodeToString(item) + "\n")
            trim()
        }
    }

    fun peek(limit: Int): List<PhoneNotificationItem> = synchronized(lock) {
        lines().take(limit).mapNotNull { line ->
            runCatching {
                WinnieApi.json.decodeFromString<PhoneNotificationItem>(line)
            }.getOrNull()
        }
    }

    fun ack(count: Int) {
        if (count <= 0) return
        synchronized(lock) {
            val remaining = lines().drop(count)
            val temp = File(file.parentFile, "${file.name}.tmp")
            temp.writeText(remaining.joinToString("") { "$it\n" })
            if (!temp.renameTo(file)) {
                file.writeText(remaining.joinToString("") { "$it\n" })
                temp.delete()
            }
        }
    }

    fun size(): Int = synchronized(lock) { lines().size }

    fun clear() {
        synchronized(lock) {
            file.delete()
        }
    }

    private fun lines(): List<String> {
        if (!file.exists()) return emptyList()
        return runCatching {
            file.readLines().filter { it.isNotBlank() }
        }.getOrDefault(emptyList())
    }

    private fun trim() {
        val lines = lines()
        if (lines.size <= MAX_ENTRIES) return
        val kept = lines.takeLast(MAX_ENTRIES)
        val temp = File(file.parentFile, "${file.name}.tmp")
        temp.writeText(kept.joinToString("") { "$it\n" })
        if (!temp.renameTo(file)) {
            file.writeText(kept.joinToString("") { "$it\n" })
            temp.delete()
        }
    }

    private companion object {
        const val MAX_ENTRIES = 2000
    }
}
