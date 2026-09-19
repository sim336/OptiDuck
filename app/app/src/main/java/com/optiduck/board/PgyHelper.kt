package com.optiduck.board

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri

/**
 * 蒲公英自动唤起辅助。
 *
 * 蒲公英官方 Android SDK 属政企/商务授权，个人无法免费集成，因此这里采用
 * "自动检测 -> 拉起官方 App -> 提示就绪"：
 * 手机需装并登录蒲公英 App，本 App 帮用户找准包名并一键拉起。
 *
 * 蒲公英多渠道分发包名差异大，故不依赖固定候选，而是动态扫描
 * 已装应用中包名含 oray/dandelion 或应用名含"蒲公英"者。
 */
object PgyHelper {

    /** 返回匹配到的蒲公英应用条目（包名 + 应用名）；未找到返回 null */
    fun installed(context: Context): Pair<String, String>? {
        val pm = context.packageManager
        @SuppressLint("QueryPermissionsNeeded")
        val infos = pm.getInstalledApplications(0).mapNotNull { ai ->
            runCatching {
                val label = pm.getApplicationLabel(ai).toString()
                val pkg = ai.packageName.lowercase()
                val matched = pkg.contains("oray") ||
                    pkg.contains("dandelion") ||
                    label.contains("蒲公英", ignoreCase = false)
                if (matched) ai.packageName to label else null
            }.getOrNull()
        }
        return infos.firstOrNull()
    }

    /** 尝试唤起蒲公英（用其主 Activity），返回是否成功；调用前先 [installed] 判空 */
    fun launch(context: Context): Boolean {
        val pkg = installed(context)?.first ?: return false
        val intent = context.packageManager.getLaunchIntentForPackage(pkg)
        return if (intent != null) {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(intent) }.isSuccess
        } else {
            false
        }
    }

    /** 引导去官网下载页 */
    fun openStore(context: Context) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://pgy.oray.com/download/"))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }
    }
}