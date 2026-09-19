package com.optiduck.board

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 功能地图页：完整复刻官方 console 功能区块，全部标注未实现。 */
@Composable
fun FuncScreen(mod: Modifier) {
    Column(
        modifier = mod
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp)
    ) {
        Text("功能地图", fontSize = 26.sp, color = Color(0xFFCDD6F4))
        Text("按官方 console 复刻，未连接时均不可用", fontSize = 12.sp, color = Color(0xFF6C7086))
        Spacer(Modifier.height(16.dp))

        Section("连接与身份") {
            RowItem("机器人名", "Midori (keeper)")
            RowItem("Release", "0.15.3")
            RowItem("API", "api v15")
            PreviewButton("connect / disconnect")
        }

        Section("视频") {
            Text("实时画面（待接入 WebRTC）", fontSize = 13.sp, color = Color(0xFF6C7086))
            RowItem("Bitrate", "—"); RowItem("Ducks", "—")
            RowItem("FPS", "—"); RowItem("Loss", "—"); RowItem("RTT", "—")
            Hint("画面上拖动可看向指定点，摄像头默认旋转 90°。")
        }

        Section("驾驶") {
            Text("前进/平移 摇杆（未接入）", fontSize = 13.sp, color = Color(0xFF6C7086))
            Text("转向 摇杆（未接入）", fontSize = 13.sp, color = Color(0xFF6C7086))
            Hint("全量偏转 0.3 m/s 与 1.5 rad/s；WASD/QE 亦可驱动。")
        }

        Section("姿态") {
            PreviewButton("enable ⇄"); PreviewButton("init")
            PreviewButton("relax"); PreviewButton("stop")
            PreviewButton("shutdown")
        }

        Section("动作技能") {
            Chips(listOf("sit ⇄ stand", "ground pick", "kick left", "kick right", "roulade"))
            Hint("一次请求执行一个技能。")
        }

        Section("音效") {
            Chips(listOf("chirp", "greet", "inquire", "alarm", "peck", "coo", "wheee (hold)"))
        }

        Section("遥测") {
            RowItem("Mode", "—"); RowItem("Policy", "—"); RowItem("Loop", "—")
            RowItem("Safety", "—"); RowItem("Requested", "—"); RowItem("Applied", "—")
            RowItem("Health", "—"); RowItem("Battery", "—"); RowItem("Temps", "—")
            Hint("robot.subscribe @2Hz；health 单独轮询。")
        }

        Section("远程终端(console)") {
            Hint("原始 JSON-RPC 发送与日志。网络类方法会被拒绝。")
            PreviewButton("send (原始调用)")
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Text(title.uppercase(), fontSize = 13.sp, color = Color(0xFF89B4FA), modifier = Modifier.padding(top = 16.dp, bottom = 6.dp))
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1E1E2E))
    ) {
        Column(Modifier.padding(14.dp), content = content)
    }
}

@Composable
private fun RowItem(key: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(key, fontSize = 13.sp, color = Color(0xFFA6ADC8))
        Text(value, fontSize = 13.sp, color = Color(0xFFCDD6F4))
    }
}

@Composable
private fun Hint(text: String) {
    Text(text, fontSize = 12.sp, color = Color(0xFF6C7086), modifier = Modifier.padding(vertical = 4.dp))
}

@Composable
private fun PreviewButton(label: String) {
    OutlinedButton(
        onClick = {},
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)
    ) {
        Text("$label · 未实现", fontSize = 13.sp)
    }
}

@Composable
private fun Chips(labels: List<String>) {
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(vertical = 6.dp)
    ) {
        labels.forEach { l ->
            FilterChip(
                selected = false,
                onClick = {},
                label = { Text(l, fontSize = 12.sp) },
                modifier = Modifier.padding(end = 6.dp)
            )
        }
    }
}