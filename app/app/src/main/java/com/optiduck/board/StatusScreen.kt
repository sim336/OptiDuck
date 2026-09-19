package com.optiduck.board

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** 状态页：轮询 /api/status 展示为指标卡片。 */
@Composable
fun StatusScreen(mod: Modifier) {
    var data by remember { mutableStateOf<JSONObject?>(null) }
    var ok by remember { mutableStateOf<Boolean?>(null) }

    LaunchedEffect(Unit) {
        while (true) {
            val (j, s) = withContext(Dispatchers.IO) { fetch() }
            data = j
            ok = s
            delay(2500)
        }
    }

    Column(mod.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Text("板子状态", fontSize = 26.sp, color = Color(0xFFCDD6F4))
        Text("每 2.5 秒刷新 · " + Prefs.statusBase(), fontSize = 12.sp, color = Color(0xFF6C7086))
        Spacer(Modifier.height(16.dp))

        if (ok == false) {
            Text("连接失败", color = Color(0xFFF38BA8), fontSize = 14.sp)
        }

        val j = data
        if (j != null) {
            MetricGrid(j)
        }
    }
}

private fun fetch(): Pair<JSONObject?, Boolean> {
    return try {
        val url = URL(Prefs.statusBase() + "/api/status")
        val conn = url.openConnection() as HttpURLConnection
        conn.connectTimeout = 3500; conn.readTimeout = 3500
        val body = conn.inputStream.bufferedReader().readText()
        conn.disconnect()
        JSONObject(body) to true
    } catch (e: Exception) {
        e.printStackTrace()
        null to false
    }
}

@Composable
private fun MetricGrid(j: JSONObject) {
    val temp = if (j.has("temp") && !j.isNull("temp")) j.getDouble("temp") else null
    val cpu = if (j.has("cpu_percent")) j.getDouble("cpu_percent") else null
    val cores = j.optInt("cpu_count", 0)
    val memUse = j.optLong("mem_used", -1)
    val memTot = j.optLong("mem_total", -1)
    val diskUse = j.optLong("disk_used", -1)
    val diskTot = j.optLong("disk_total", -1)
    val loadArr = j.optJSONArray("load")
    val up = j.optLong("uptime_s", -1)
    val ips = j.optJSONArray("ips")

    Row {
        Metric("温度", temp?.let { "${it}${"\u00B0"}C" } ?: "未读取",
            warn = temp != null && temp > 65, mod = Modifier.weight(1f))
        Spacer(Modifier.width(10.dp))
        Metric("CPU", cpu?.let { "${"%.1f".format(it)}%\n$cores 核" } ?: "—",
            warn = cpu != null && cpu > 70, mod = Modifier.weight(1f))
    }
    Spacer(Modifier.height(12.dp))
    Row {
        Metric("内存",
            if (memTot > 0 && memUse >= 0) "${mb(memUse)} / ${mb(memTot)}" else "—",
            warn = memTot > 0 && memUse * 100.0 / memTot > 80, mod = Modifier.weight(1f))
        Spacer(Modifier.width(10.dp))
        Metric("磁盘",
            if (diskTot > 0 && diskUse >= 0) "${mb(diskUse)} / ${mb(diskTot)}" else "—",
            warn = diskTot > 0 && diskUse * 100.0 / diskTot > 85, mod = Modifier.weight(1f))
    }
    Spacer(Modifier.height(12.dp))
    Metric("负载", loadArr?.let { "${it.get(0)}  ${it.get(1)}  ${it.get(2)}" } ?: "—",
        warn = loadArr != null && loadArr.get(0).toString().toDouble() > cores.toDouble(), mod = Modifier.fillMaxWidth())
    Spacer(Modifier.height(12.dp))
    Metric("运行时长", if (up >= 0) fmtUptime(up) else "—",
        mod = Modifier.fillMaxWidth())
    Spacer(Modifier.height(12.dp))
    Metric("IP", ips?.let { (0 until it.length()).joinToString("  ") { i -> it.getString(i) } } ?: "—",
        mod = Modifier.fillMaxWidth())
}

@Composable
private fun Metric(label: String, value: String, mod: Modifier = Modifier, warn: Boolean = false) {
    Card(
        modifier = mod,
        colors = CardDefaults.cardColors(containerColor = Color(0xFF1E1E2E)),
        border = if (warn) BorderStroke(1.dp, Color(0xFFF38BA8)) else null
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(label, fontSize = 12.sp, color = Color(0xFFA6ADC8))
            Spacer(Modifier.height(6.dp))
            Text(value, fontSize = 16.sp, color = if (warn) Color(0xFFF38BA8) else Color(0xFFCDD6F4))
        }
    }
}

private fun mb(b: Long): String {
    val m = b / (1024.0 * 1024.0)
    return if (m >= 1024) "${"%.1f".format(m / 1024)}G" else "${"%.0f".format(m)}M"
}

private fun fmtUptime(sec: Long): String {
    val d = sec / 86400; val h = (sec % 86400) / 3600; val m = (sec % 3600) / 60
    return if (d > 0) "${d}天 ${h}时 ${m}分" else "${h}时 ${m}分"
}