package com.optiduck.board

import android.app.Activity
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material.icons.filled.CloudQueue
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector

@Composable
fun AppRoot(app: Activity) {
    var tab by rememberSaveable { mutableStateOf(0) }
    val tabs = listOf("连接", "状态", "功能", "终端")
    val icons = listOf(
        Icons.Filled.Settings,
        Icons.Filled.CloudQueue,
        Icons.AutoMirrored.Filled.MenuBook,
        Icons.Filled.Terminal
    )
    Scaffold(
        containerColor = androidx.compose.ui.graphics.Color(0xFF11111B),
        bottomBar = {
            NavigationBar(containerColor = androidx.compose.ui.graphics.Color(0xFF181825)) {
                tabs.forEachIndexed { i, label ->
                    NavigationBarItem(
                        selected = tab == i,
                        onClick = { tab = i },
                        icon = { Icon(icons[i], contentDescription = label) },
                        label = { Text(label) }
                    )
                }
            }
        }
    ) { padding ->
        val mod = Modifier.padding(padding)
        when (tab) {
            0 -> ConnectScreen(mod)
            1 -> StatusScreen(mod)
            2 -> FuncScreen(mod)
            3 -> TerminalScreen(mod)
        }
    }
}