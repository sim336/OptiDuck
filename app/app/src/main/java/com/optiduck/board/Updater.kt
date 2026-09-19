package com.optiduck.board

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * 在线更新：从板子服务检查版本、下载 APK、拉起系统安装。
 *
 * 流程：
 *   1. check()    -> GET {base}/update/check 拿版本元数据
 *   2. download() -> 拉取 {base}/apkPath 到应用缓存
 *   3. install()  -> FileProvider 生成 content:// uri，ACTION_VIEW 拉起安装
 */
object Updater {

    /** @return UpdateInfo 若服务器有新版本（versionCode > 当前包）；否则 null */
    fun check(context: Context): UpdateInfo? {
        return try {
            val url = URL(Prefs.statusBase() + "/update/check")
            val conn = url.openConnection() as HttpURLConnection
            conn.connectTimeout = 4000; conn.readTimeout = 4000
            val body = conn.inputStream.bufferedReader().readText()
            conn.disconnect()
            val j = JSONObject(body)
            val newCode = j.optInt("versionCode", 0)
            val current = context.packageManager.getPackageInfo(context.packageName, 0).versionCode
            if (newCode > current) {
                UpdateInfo(
                    versionCode = newCode,
                    versionName = j.optString("versionName", ""),
                    size = j.optLong("size", 0),
                    apkPath = j.optString("apkPath", "/download"),
                    changelog = j.optString("changelog", ""),
                )
            } else null
        } catch (e: Exception) {
            null
        }
    }

    /** 下载 APK 到缓存，返回文件 */
    fun download(context: Context, info: UpdateInfo): File {
        val out = File(context.cacheDir, "botstatus-update.apk")
        val url = URL(Prefs.statusBase() + info.apkPath)
        val conn = url.openConnection() as HttpURLConnection
        conn.connectTimeout = 8000; conn.readTimeout = 15000
        conn.inputStream.use { ins ->
            out.outputStream().use { ous -> ins.copyTo(ous) }
        }
        return out
    }

    /** 用 FileProvider 拉起系统安装器 */
    fun install(context: Context, file: File) {
        val uri: Uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        val intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, "application/vnd.android.package-archive")
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }
}

data class UpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val size: Long,
    val apkPath: String,
    val changelog: String = "",
)