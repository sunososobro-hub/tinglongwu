package com.tinglongwu.mobile

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/**
 * #1864: is the phone on Wi-Fi (unmetered) or mobile data (metered)? Read-only; opens no
 * connection. Needs ACCESS_NETWORK_STATE, the one extra network permission
 * scripts/check_mobile_network.py allows. Returns "unmetered", "metered", "none" or "unknown".
 */
object NetworkProbe {
    fun kind(ctx: Context): String = try {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        val caps = cm?.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        when {
            cm == null -> "unknown"
            caps == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) -> "none"
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) -> "unmetered"
            else -> "metered"
        }
    } catch (e: SecurityException) {
        "unknown"
    }
}
