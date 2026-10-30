package dev.winnie.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class User(
    val id: String,
    val username: String,
    @SerialName("display_name") val displayName: String? = null,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
    @SerialName("expires_at") val expiresAt: String,
    val user: User,
)

@Serializable
data class Meeting(
    val id: String,
    val title: String,
    val status: String,
    val stage: String? = null,
    val source: String = "file",
    @SerialName("local_language") val localLanguage: String = "zh",
    @SerialName("audio_duration_ms") val audioDurationMs: Int? = null,
    @SerialName("audio_deleted_at") val audioDeletedAt: String? = null,
    val error: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
) {
    val isReady: Boolean get() = status == "ready"
    val isFailed: Boolean get() = status == "failed"
    val isProcessing: Boolean get() = !isReady && !isFailed && status != "created"
}

@Serializable
data class MeetingList(
    val items: List<Meeting> = emptyList(),
    val total: Int = 0,
)

@Serializable
data class Segment(
    val seq: Int,
    @SerialName("start_ms") val startMs: Int? = null,
    @SerialName("end_ms") val endMs: Int? = null,
    val speaker: String? = null,
    val text: String,
    @SerialName("detected_language") val detectedLanguage: String? = null,
    @SerialName("translated_text") val translatedText: String? = null,
    @SerialName("translation_status") val translationStatus: String = "skipped",
)

@Serializable
data class Transcript(
    @SerialName("meeting_id") val meetingId: String,
    val segments: List<Segment> = emptyList(),
)

@Serializable
data class Topic(
    val title: String,
    val points: List<String> = emptyList(),
)

@Serializable
data class Decision(
    val text: String,
    val context: String = "",
)

@Serializable
data class ActionItem(
    val text: String,
    val owner: String? = null,
    @SerialName("due_date") val dueDate: String? = null,
    val status: String = "open",
)

@Serializable
data class Highlight(
    @SerialName("segment_seq") val segmentSeq: Int,
    val text: String,
)

@Serializable
data class Minutes(
    @SerialName("meeting_id") val meetingId: String,
    val summary: String,
    val topics: List<Topic> = emptyList(),
    val decisions: List<Decision> = emptyList(),
    @SerialName("action_items") val actionItems: List<ActionItem> = emptyList(),
    val highlights: List<Highlight> = emptyList(),
    val model: String = "",
    @SerialName("prompt_version") val promptVersion: String = "",
    @SerialName("created_at") val createdAt: String,
)
