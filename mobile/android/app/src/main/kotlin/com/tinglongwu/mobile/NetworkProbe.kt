package com.tinglongwu.mobile

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities

/**
 * #1864: what the active network is. Read-only; opens no connection. Needs ACCESS_NETWORK_STATE,
 * the one extra network permission scripts/check_mobile_network.py allows. Reports raw facts only
 * (internet, notMetered, wifi, ethernet, cellular); the Wi-Fi / mobile-data decision is Dart's
 * netKindFrom() in base_download.dart, which the Dart tests cover (Android can mark 5G cellular as
 * NOT_METERED, so metering alone must not start a 1.3 GB download). Null when it cannot tell.
 */
object NetworkProbe {
    /**
     * #1879: the unmetered Wi-Fi / Ethernet Network (no cellular transport) an automatic download
     * must stay on; null when none is up. Same rule as Dart's netKindFrom(). Opens no connection.
     */
    @Suppress("DEPRECATION")
    fun unmeteredNetwork(ctx: Context): Network? = try {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        cm?.allNetworks?.firstOrNull { n ->
            val c = cm.getNetworkCapabilities(n)
            c != null &&
                c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                c.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) &&
                !c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) &&
                (c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) || c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
        }
    } catch (e: SecurityException) {
        null
    }

    fun facts(ctx: Context): Map<String, Boolean>? = try {
        val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        if (cm == null) {
            null
        } else {
            val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) }
            mapOf(
                "internet" to (caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true),
                "notMetered" to (caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED) == true),
                "wifi" to (caps?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true),
                "ethernet" to (caps?.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) == true),
                "cellular" to (caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true),
            )
        }
    } catch (e: SecurityException) {
        null
    }
}
