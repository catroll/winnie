package dev.winnie.app.push

import dev.winnie.app.data.PushEvent

/** What to do with one frame from the push stream. */
sealed interface PushAction {
    /** A confirmed phone command: run a sync so the phone executes it now. */
    data object RunSync : PushAction

    /** A user-facing notification. */
    data class ShowNotification(val event: PushEvent) : PushAction

    /** A heartbeat, a ready marker or anything this version does not know. */
    data object Ignore : PushAction
}

/**
 * The routing rule, separated from the service so it can be tested without
 * Android and so the decision stays in one place.
 */
fun pushActionFor(event: PushEvent): PushAction = when {
    event.type != "event" -> PushAction.Ignore
    event.kind == "sync" -> PushAction.RunSync
    else -> PushAction.ShowNotification(event)
}
