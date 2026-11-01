package dev.winnie.app.ui.push

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import dev.winnie.app.AppContainer
import dev.winnie.app.R

/**
 * Alerts and the background connection.
 *
 * The screen is explicit about the trade: a self-hosted socket means no vendor
 * push service, but it also means a permanent notification and battery use, and
 * some phone vendors kill it anyway unless battery optimization is disabled.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PushSettingsScreen(
    container: AppContainer,
    onBack: () -> Unit,
) {
    val appContext = LocalContext.current.applicationContext
    val viewModel: PushSettingsViewModel = viewModel(
        factory = viewModelFactory {
            initializer { PushSettingsViewModel(container, appContext) }
        },
    )
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            viewModel.setEnabled(true)
        } else {
            viewModel.permissionDenied()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Alerts & background",
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(painterResource(R.drawable.ic_back), contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh) {
                        Icon(
                            painterResource(R.drawable.ic_refresh),
                            contentDescription = "Refresh",
                        )
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Explanation() }
            item {
                ConnectionCard(
                    ui = ui,
                    onToggle = { enabled ->
                        if (!enabled) {
                            viewModel.setEnabled(false)
                        } else if (needsNotificationPermission(appContext, ui)) {
                            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                        } else {
                            viewModel.setEnabled(true)
                        }
                    },
                )
            }
            item { TestCard(ui, onSend = viewModel::sendTest) }
            item {
                PermissionCard(
                    ui = ui,
                    onOpenNotificationSettings = { openNotificationSettings(appContext) },
                    onOpenBatterySettings = { openBatterySettings(appContext) },
                )
            }
            item { ServerCard(ui) }
            item { BackgroundNote() }
            ui.message?.let { message ->
                item {
                    Text(
                        message,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun Explanation() {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(Modifier.padding(16.dp)) {
            Text("How this works", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(6.dp))
            Text(
                "Winnie keeps one connection open from a foreground service so a " +
                    "reminder or a finished task reaches you without polling. " +
                    "Android shows a persistent, low-priority notification while " +
                    "that connection runs, and it costs some battery.",
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "There is no Google or vendor push service: the connection is " +
                    "between this phone and your Winnie server. That is why it " +
                    "needs the foreground service.",
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ConnectionCard(ui: PushSettingsUi, onToggle: (Boolean) -> Unit) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Background connection", style = MaterialTheme.typography.titleMedium)
                    Text(
                        statusLabel(ui),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (ui.connection == "connected") {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
                Switch(checked = ui.enabled, onCheckedChange = onToggle)
            }
            if (ui.error.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    ui.error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            Text(
                "Last event: ${ui.lastEventLabel} · cursor ${ui.lastSeq}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun TestCard(ui: PushSettingsUi, onSend: () -> Unit) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Check it", style = MaterialTheme.typography.titleMedium)
            Text(
                "The server publishes a notification to this account. It arrives " +
                    "immediately when the connection is up, and from the backlog on " +
                    "the next connect if it is not.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Button(onClick = onSend, enabled = ui.enabled) { Text("Send test notification") }
        }
    }
}

@Composable
private fun PermissionCard(
    ui: PushSettingsUi,
    onOpenNotificationSettings: () -> Unit,
    onOpenBatterySettings: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Device permissions", style = MaterialTheme.typography.titleMedium)
            PermissionRow(
                title = "Notifications",
                detail = if (ui.notificationsAllowed) {
                    "Allowed"
                } else {
                    "Blocked — alerts cannot be shown"
                },
                ok = ui.notificationsAllowed,
                action = "Settings",
                onOpen = onOpenNotificationSettings,
            )
            PermissionRow(
                title = "Battery optimization",
                detail = if (ui.batteryUnrestricted) {
                    "Winnie is unrestricted"
                } else {
                    "Optimized — some phones will kill the connection"
                },
                ok = ui.batteryUnrestricted,
                action = "Unrestrict",
                onOpen = onOpenBatterySettings,
            )
        }
    }
}

@Composable
private fun PermissionRow(
    title: String,
    detail: String,
    ok: Boolean,
    action: String,
    onOpen: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyMedium)
            Text(
                detail,
                style = MaterialTheme.typography.labelSmall,
                color = if (ok) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.error
                },
            )
        }
        TextButton(onClick = onOpen) { Text(action) }
    }
}

@Composable
private fun ServerCard(ui: PushSettingsUi) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Server side", style = MaterialTheme.typography.titleMedium)
            Text(
                "Connected devices: ${ui.serverConnected} · pending notifications: ${ui.pending}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "A notification is stored before it is sent, so a phone that was " +
                    "offline still receives it on the next connect. The server keeps " +
                    "it for the configured retention window.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun BackgroundNote() {
    Text(
        "If reminders stop arriving, open the app once. Some vendors end " +
            "background services aggressively even with battery optimization " +
            "disabled, and the boot receiver cannot restart the service until the " +
            "phone finishes booting.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

private fun statusLabel(ui: PushSettingsUi): String {
    if (!ui.enabled) return "Off — no reminders"
    return when (ui.connection) {
        "connected" -> "Connected"
        "connecting" -> "Connecting…"
        "signed out" -> "Signed out"
        "blocked" -> "Could not start in the background"
        "stopped" -> "Stopped"
        else -> "Disconnected"
    }
}

private fun needsNotificationPermission(context: Context, ui: PushSettingsUi): Boolean {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return false
    return !ui.notificationsAllowed &&
        context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
        android.content.pm.PackageManager.PERMISSION_GRANTED
}

private fun openNotificationSettings(context: Context) {
    val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
    } else {
        Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.parse("package:${context.packageName}"),
        )
    }
    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}

private fun openBatterySettings(context: Context) {
    val request = Intent(
        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
        Uri.parse("package:${context.packageName}"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(request) }.onFailure {
        val fallback = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(fallback) }
    }
}
