package com.tinglongwu.mobile

import android.app.ActivityManager
import android.content.Context
import android.os.Build
import java.io.File

/** Device facts for the recommendations in 進階功能 (#1065). Read-only, no permissions. */
object DeviceProbe {
    fun collect(ctx: Context): Map<String, Any?> {
        val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val mi = ActivityManager.MemoryInfo()
        am.getMemoryInfo(mi)
        val cores = Runtime.getRuntime().availableProcessors()
        val soc = if (Build.VERSION.SDK_INT >= 31 && Build.SOC_MODEL.isNotEmpty() && Build.SOC_MODEL != "unknown")
            "${Build.SOC_MANUFACTURER} ${Build.SOC_MODEL}".trim() else Build.HARDWARE
        val version = try {
            ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName
        } catch (_: Exception) { null }
        return mapOf(
            "cores" to cores,
            "bigCores" to bigCores(cores),
            "abi" to Build.SUPPORTED_ABIS.firstOrNull(),
            "totalRamMb" to (mi.totalMem / (1024 * 1024)).toInt(),
            "availRamMb" to (mi.availMem / (1024 * 1024)).toInt(),
            "sdk" to Build.VERSION.SDK_INT,
            "soc" to soc,
            "model" to Build.MODEL,
            "appVersion" to version,
        )
    }

    /** Cores whose max frequency is within 70 % of the fastest; null when sysfs is unreadable. */
    private fun bigCores(cores: Int): Int? {
        val freqs = (0 until cores).mapNotNull {
            try {
                File("/sys/devices/system/cpu/cpu$it/cpufreq/cpuinfo_max_freq").readText().trim().toLong()
            } catch (_: Exception) { null }
        }
        val top = freqs.maxOrNull() ?: return null
        if (top <= 0) return null
        return freqs.count { it >= top * 0.7 }
    }
}
