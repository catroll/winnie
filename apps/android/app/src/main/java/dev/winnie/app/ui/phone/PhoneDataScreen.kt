package dev.winnie.app.ui.phone

import android.content.Context
import android.content.Intent
import android.net.Uri
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
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
import dev.winnie.app.phone.PhonePermissions
import dev.winnie.app.phone.PhoneScope

/**
 * Phone-data consent and status.
 *
 * The screen is deliberately blunt about what leaves the phone: the server
 * stores this data in clear text so the assistant can read it, and the app is
 * for personal sideloading because Google Play restricts SMS and call-log
 * access. Each scope is an explicit opt-in with its own permission prompt.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PhoneDataScreen(
    container: AppContainer,
    bottomBar: @Composable () -> Unit,
) {
    val appContext = LocalContext.current.applicationContext
    val viewModel: PhoneDataViewModel = viewModel(
        factory = viewModelFactory {
            initializer { PhoneDataViewModel(container, appContext) }
        },
    )
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var pendingScope by remember { mutableStateOf<PhoneScope?>(null) }
    var confirmWipe by remember { mutableStateOf(false) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { result ->
        val scope = pendingScope
        pendingScope = null
        if (scope != null) {
            if (result.values.all { granted -> granted }) {
                viewModel.enableScope(scope)
            } else {
                viewModel.permissionDenied(scope)
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "Phone data",
                        maxLines = 1,
                        softWrap = false,
                        overflow = TextOverflow.Ellipsis,
                    )
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
        bottomBar = bottomBar,
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { PrivacyNotice() }
            item {
                SyncCard(
                    ui = ui,
                    onMasterChange = viewModel::setMaster,
                    onSyncNow = viewModel::syncNow,
                )
            }
            item { SectionTitle("What to share") }
            items(ui.scopes, key = { it.scope.key }) { item ->
                ScopeRow(
                    item = item,
                    onToggle = { checked ->
                        if (!checked) {
                            viewModel.disableScope(item.scope)
                        } else if (
                            item.scope.needsNotificationAccess &&
                            !PhonePermissions.notificationAccess(appContext)
                        ) {
                            openNotificationSettings(appContext)
                        } else if (item.ready) {
                            viewModel.enableScope(item.scope)
                        } else {
                            pendingScope = item.scope
                            permissionLauncher.launch(
                                item.scope.runtimePermissions.toTypedArray()
                            )
                        }
                    },
                )
            }
            item { SpecialAccessCard(appContext) }
            item {
                DangerCard(
                    deviceName = ui.deviceName,
                    pendingCommands = ui.pendingCommands,
                    onWipe = { confirmWipe = true },
                )
            }
            item { RetentionNote() }
        }
    }

    if (confirmWipe) {
        AlertDialog(
            onDismissRequest = { confirmWipe = false },
            title = { Text("Delete server copies?") },
            text = {
                Text(
                    "Every synced record for this phone is deleted from the server. " +
                        "The phone stays registered and the next sync uploads the " +
                        "selected scopes again."
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmWipe = false
                        viewModel.wipeServerData()
                    },
                ) { Text("Delete") }
            },
            dismissButton = {
                TextButton(onClick = { confirmWipe = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun PrivacyNotice() {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = MaterialTheme.shapes.medium,
    ) {
        Column(Modifier.padding(16.dp)) {
            Text("Read this first", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(6.dp))
            Text(
                "Everything you enable here leaves this phone. The Winnie server " +
                    "stores it in clear text in its database so the assistant can " +
                    "read and search it, and keeps it for the retention window " +
                    "configured on the server. Use a server you control, over HTTPS " +
                    "or your own network.",
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "This build is for personal sideloading. Google Play restricts apps " +
                    "that read SMS or the call log.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun SyncCard(
    ui: PhoneDataUi,
    onMasterChange: (Boolean) -> Unit,
    onSyncNow: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Sync", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "Last sync: ${ui.lastSyncLabel}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (ui.lastResult.isNotBlank()) {
                        Text(
                            ui.lastResult,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Switch(checked = ui.master, onCheckedChange = onMasterChange)
            }
            if (ui.deviceName.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "Device on server: ${ui.deviceName}",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            if (ui.counts.isNotEmpty()) {
                Text(
                    "Stored: " + ui.counts.entries.joinToString(" · ") {
                        "${it.key} ${it.value}"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (ui.lastError.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    ui.lastError,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = onSyncNow,
                    enabled = ui.master && !ui.running,
                ) {
                    Text(if (ui.running) "Syncing…" else "Sync now")
                }
                if (ui.running) {
                    Spacer(Modifier.width(12.dp))
                    CircularProgressIndicator(Modifier.width(20.dp).height(20.dp))
                }
            }
            ui.message?.let { message ->
                Spacer(Modifier.height(8.dp))
                Text(
                    message,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun ScopeRow(item: PhoneScopeUi, onToggle: (Boolean) -> Unit) {
    Card {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(item.scope.title, style = MaterialTheme.typography.titleSmall)
                Text(
                    item.scope.description,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                if (item.detail.isNotBlank()) {
                    Text(
                        item.detail,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Spacer(Modifier.width(12.dp))
            Switch(checked = item.enabled, onCheckedChange = onToggle)
        }
    }
}

@Composable
private fun SpecialAccessCard(context: Context) {
    val notificationAccess = PhonePermissions.notificationAccess(context)
    val backgroundLocation = PhonePermissions.backgroundLocation(context)
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Special access", style = MaterialTheme.typography.titleMedium)
            Text(
                "These two are granted in system settings, not with a normal permission prompt.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            AccessRow(
                title = "Notification access",
                granted = notificationAccess,
                action = "Open settings",
                onOpen = { openNotificationSettings(context) },
            )
            HorizontalDivider()
            AccessRow(
                title = "Location in the background",
                granted = backgroundLocation,
                action = "Open settings",
                onOpen = { openAppSettings(context) },
            )
        }
    }
}

@Composable
private fun AccessRow(
    title: String,
    granted: Boolean,
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
                if (granted) "Granted" else "Not granted",
                style = MaterialTheme.typography.labelSmall,
                color = if (granted) {
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
private fun DangerCard(
    deviceName: String,
    pendingCommands: Int,
    onWipe: () -> Unit,
) {
    Card {
        Column(Modifier.padding(16.dp)) {
            Text("Server copies", style = MaterialTheme.typography.titleMedium)
            Text(
                if (deviceName.isBlank()) {
                    "This phone has not synced yet."
                } else {
                    "$deviceName · $pendingCommands pending assistant action(s)"
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            OutlinedButton(onClick = onWipe) { Text("Delete data on server") }
        }
    }
}

@Composable
private fun RetentionNote() {
    Text(
        "Turning a scope off stops future uploads but does not remove what the " +
            "server already has. Use \"Delete data on server\" for that. The server " +
            "also expires records on its own retention schedule.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleSmall,
        modifier = Modifier.padding(top = 4.dp),
    )
}

private fun openNotificationSettings(context: Context) {
    val intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}

private fun openAppSettings(context: Context) {
    val intent = Intent(
        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
        Uri.parse("package:${context.packageName}"),
    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}
