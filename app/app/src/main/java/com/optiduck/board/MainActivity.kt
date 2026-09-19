package com.optiduck.board

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val DarkColors = darkColorScheme(
    primary = Color(0xFF89B4FA),
    onPrimary = Color(0xFF11111B),
    background = Color(0xFF11111B),
    surface = Color(0xFF1E1E2E),
    onBackground = Color(0xFFCDD6F4),
    onSurface = Color(0xFFCDD6F4),
    secondary = Color(0xFF94E2D5),
    error = Color(0xFFF38BA8),
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            BoardStatusTheme {
                AppRoot(app = this)
            }
        }
    }
}

@Composable
fun BoardStatusTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else DarkColors,
        content = content,
    )
}