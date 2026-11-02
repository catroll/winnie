package dev.winnie.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * The assistant contract from `packages/winnie-assistant`. Field names mirror
 * `schemas.py`, including the snake_case the server emits; the client never
 * renames them.
 */

@Serializable
data class AssistantChatReply(
    @SerialName("conversation_id") val conversationId: String,
    val reply: String,
    @SerialName("tools_used") val toolsUsed: List<String> = emptyList(),
    @SerialName("usage_tokens") val usageTokens: Double = 0.0,
)

@Serializable
data class AssistantConversationSummary(
    val id: String,
    val title: String = "",
    @SerialName("created_at") val createdAt: String = "",
    /** `user` for a person, `task` for a scheduled run. */
    val source: String = "",
    /** Which entry point started it: admin, web or api. */
    val channel: String = "",
    @SerialName("message_count") val messageCount: Int = 0,
    val preview: String = "",
)

@Serializable
data class AssistantConversationList(
    val items: List<AssistantConversationSummary> = emptyList(),
    val total: Int = 0,
)

@Serializable
data class AssistantMessage(
    /** user, assistant or tool. */
    val role: String,
    /** The tool name for role=tool; empty otherwise. */
    val name: String = "",
    val text: String = "",
    val at: String = "",
)

@Serializable
data class AssistantConversation(
    val id: String,
    val title: String = "",
    @SerialName("created_at") val createdAt: String = "",
    val source: String = "",
    val channel: String = "",
    val messages: List<AssistantMessage> = emptyList(),
)

@Serializable
internal data class AssistantChatRequest(
    val message: String,
    @SerialName("conversation_id") val conversationId: String? = null,
    /**
     * Where the turn arrived. The server accepts admin, web and api only; a
     * native client is an API client, and the `api` channel keeps phone
     * conversations distinguishable in the console history.
     *
     * No default: kotlinx.serialization omits defaulted properties, and the
     * channel must be on the wire rather than left to the server's default.
     */
    val channel: String,
    /**
     * How the owner produced this turn. A voice turn asks the server for a
     * reply written to be heard rather than read. Defaulted to "text" so a
     * typed turn omits it from the wire and older servers keep working.
     */
    val input: String = "text",
)

/** The transcript of one recorded voice turn. */
@Serializable
data class VoiceTranscript(val text: String = "")
