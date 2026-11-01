package dev.winnie.app.phone.sources

import android.content.Context
import android.provider.ContactsContract
import dev.winnie.app.data.PhoneContactBirthday
import dev.winnie.app.data.PhoneContactItem

/**
 * Reads the contacts aggregate with three bulk queries rather than one query
 * per contact: phones, emails and organisations are grouped by contact id in
 * memory. A few thousand contacts cost milliseconds instead of a database
 * round trip each.
 */
class ContactsSource(private val context: Context) {

    fun readAll(): List<PhoneContactItem> {
        val resolver = context.contentResolver
        val contacts = linkedMapOf<Long, MutableContact>()

        // The contact list is uploaded as a full snapshot, so a failed read
        // must raise rather than return an empty list: an empty snapshot would
        // tell the server every contact was deleted. The per-detail queries
        // below stay guarded — a missing email column costs an email, not the
        // whole contact.
        val contactCursor = resolver.query(
            ContactsContract.Contacts.CONTENT_URI,
            arrayOf(
                ContactsContract.Contacts._ID,
                ContactsContract.Contacts.DISPLAY_NAME_PRIMARY,
                ContactsContract.Contacts.STARRED,
            ),
            null,
            null,
            "${ContactsContract.Contacts.DISPLAY_NAME_PRIMARY} ASC",
        ) ?: return emptyList()

        contactCursor.use {
            val id = it.getColumnIndexOrThrow(ContactsContract.Contacts._ID)
            val name = it.getColumnIndex(ContactsContract.Contacts.DISPLAY_NAME_PRIMARY)
            val starred = it.getColumnIndex(ContactsContract.Contacts.STARRED)
            while (it.moveToNext()) {
                val contactId = it.getLong(id)
                contacts[contactId] = MutableContact(
                    id = contactId,
                    displayName = if (name >= 0) it.getString(name).orEmpty() else "",
                    starred = starred >= 0 && it.getInt(starred) != 0,
                )
            }
        }

        readNumbers(contacts)
        readEmails(contacts)
        readOrganizations(contacts)
        readBirthdays(contacts)

        return contacts.values.map {
            PhoneContactItem(
                externalId = it.id.toString(),
                displayName = it.displayName,
                numbers = it.numbers.toList(),
                emails = it.emails.toList(),
                organization = it.organization,
                starred = it.starred,
                birthdays = it.birthdays.toList(),
            )
        }
    }

    private fun readNumbers(contacts: MutableMap<Long, MutableContact>) {
        val cursor = try {
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                ),
                null,
                null,
                null,
            )
        } catch (_: Exception) {
            return
        } ?: return
        cursor.use {
            val contactId = it.getColumnIndex(
                ContactsContract.CommonDataKinds.Phone.CONTACT_ID
            )
            val number = it.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
            if (contactId < 0 || number < 0) return
            while (it.moveToNext()) {
                val target = contacts[it.getLong(contactId)] ?: continue
                it.getString(number)?.takeIf { value -> value.isNotBlank() }?.let { value ->
                    target.numbers += value
                }
            }
        }
    }

    private fun readEmails(contacts: MutableMap<Long, MutableContact>) {
        val cursor = try {
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Email.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Email.ADDRESS,
                ),
                null,
                null,
                null,
            )
        } catch (_: Exception) {
            return
        } ?: return
        cursor.use {
            val contactId = it.getColumnIndex(
                ContactsContract.CommonDataKinds.Email.CONTACT_ID
            )
            val address = it.getColumnIndex(ContactsContract.CommonDataKinds.Email.ADDRESS)
            if (contactId < 0 || address < 0) return
            while (it.moveToNext()) {
                val target = contacts[it.getLong(contactId)] ?: continue
                it.getString(address)?.takeIf { value -> value.isNotBlank() }?.let { value ->
                    target.emails += value
                }
            }
        }
    }

    private fun readOrganizations(contacts: MutableMap<Long, MutableContact>) {
        val cursor = try {
            context.contentResolver.query(
                ContactsContract.Data.CONTENT_URI,
                arrayOf(
                    ContactsContract.Data.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Organization.COMPANY,
                ),
                "${ContactsContract.Data.MIMETYPE} = ?",
                arrayOf(ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE),
                null,
            )
        } catch (_: Exception) {
            return
        } ?: return
        cursor.use {
            val contactId = it.getColumnIndex(ContactsContract.Data.CONTACT_ID)
            val company = it.getColumnIndex(
                ContactsContract.CommonDataKinds.Organization.COMPANY
            )
            if (contactId < 0 || company < 0) return
            while (it.moveToNext()) {
                val target = contacts[it.getLong(contactId)] ?: continue
                it.getString(company)?.takeIf { value -> value.isNotBlank() }?.let { value ->
                    if (target.organization.isBlank()) target.organization = value
                }
            }
        }
    }

    /**
     * Birthdays and anniversaries, read from the provider's `Event` rows.
     *
     * `Event.START_DATE` is `YYYY-MM-DD`, or `--MM-DD` when the owner never
     * entered the year; the server stores month/day/year separately, so the
     * parsing stays next to the provider whose format this is. A provider that
     * hides its birthday calendar still keeps these rows, which is why they are
     * worth more than the calendar alone.
     */
    private fun readBirthdays(contacts: MutableMap<Long, MutableContact>) {
        val cursor = try {
            context.contentResolver.query(
                ContactsContract.Data.CONTENT_URI,
                arrayOf(
                    ContactsContract.Data.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Event.TYPE,
                    ContactsContract.CommonDataKinds.Event.START_DATE,
                ),
                "${ContactsContract.Data.MIMETYPE} = ?",
                arrayOf(ContactsContract.CommonDataKinds.Event.CONTENT_ITEM_TYPE),
                null,
            )
        } catch (_: Exception) {
            return
        } ?: return
        cursor.use {
            val contactId = it.getColumnIndex(ContactsContract.Data.CONTACT_ID)
            val type = it.getColumnIndex(ContactsContract.CommonDataKinds.Event.TYPE)
            val start = it.getColumnIndex(ContactsContract.CommonDataKinds.Event.START_DATE)
            if (contactId < 0 || type < 0 || start < 0) return
            while (it.moveToNext()) {
                val target = contacts[it.getLong(contactId)] ?: continue
                val birthday = parseBirthday(
                    it.getString(start).orEmpty(),
                    it.getInt(type),
                ) ?: continue
                if (birthday !in target.birthdays) target.birthdays += birthday
            }
        }
    }

    private fun parseBirthday(raw: String, eventType: Int): PhoneContactBirthday? {
        val parts = raw.trim().split("-")
        if (parts.size != 3) return null
        val month = parts[1].toIntOrNull() ?: return null
        val day = parts[2].toIntOrNull() ?: return null
        val year = parts[0].takeIf { it.length == 4 && it != "0000" }?.toIntOrNull()
        if (month !in 1..12 || day !in 1..31) return null
        return PhoneContactBirthday(
            month = month,
            day = day,
            year = year,
            type = when (eventType) {
                ContactsContract.CommonDataKinds.Event.TYPE_BIRTHDAY -> "birthday"
                ContactsContract.CommonDataKinds.Event.TYPE_ANNIVERSARY -> "anniversary"
                else -> "other"
            },
        )
    }

    /**
     * A cheap token for "did the contact list change?". `CONTACT_LAST_UPDATED_TIMESTAMP`
     * is bumped by the provider on an edit, so a count plus its maximum catches
     * adds, deletes and edits without reading every contact. A provider without
     * the column falls back to the aggregate count.
     */
    fun fingerprint(): String? {
        val cursor = try {
            context.contentResolver.query(
                ContactsContract.Contacts.CONTENT_URI,
                arrayOf(
                    ContactsContract.Contacts._ID,
                    ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP,
                ),
                null,
                null,
                null,
            )
        } catch (_: Exception) {
            null
        } ?: return null
        return cursor.use {
            var count = 0
            var newest = 0L
            val updated = it.getColumnIndex(
                ContactsContract.Contacts.CONTACT_LAST_UPDATED_TIMESTAMP,
            )
            while (it.moveToNext()) {
                count++
                if (updated >= 0) newest = maxOf(newest, it.getLong(updated))
            }
            "count:$count:updated:$newest"
        }
    }

    private data class MutableContact(
        val id: Long,
        val displayName: String,
        val starred: Boolean,
        val numbers: MutableSet<String> = linkedSetOf(),
        val emails: MutableSet<String> = linkedSetOf(),
        var organization: String = "",
        val birthdays: MutableList<PhoneContactBirthday> = mutableListOf(),
    )
}
