package com.tinglongwu.mobile

import android.os.Handler
import android.os.Looper
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * The only class that opens a network connection (#1567; scripts/check_mobile_network.py enforces it).
 * Downloads one pack zip the user tapped, only from this repo's release assets, with progress,
 * cancel, and a size + SHA-256 check before the file is handed back. Unzipping and installing the
 * files stays in the import path (ModelImporter / Dart), which checks every file again.
 */
class PackDownloader {
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var cancelled = false
    private val busy = java.util.concurrent.atomic.AtomicBoolean(false)

    fun cancel() { cancelled = true }

    /** Args: url, dest (file path), size (expected bytes), sha256 (expected hex). Returns dest. */
    fun download(call: MethodCall, result: MethodChannel.Result, progress: (Long, Long) -> Unit) {
        val url = call.argument<String>("url") ?: ""
        val dest = File(call.argument<String>("dest") ?: "")
        val size = (call.argument<Number>("size") ?: -1).toLong()
        val sha = (call.argument<String>("sha256") ?: "").lowercase()
        if (!allowedStart(url)) { result.error("BAD_URL", "not a release asset of this repo: $url", null); return }
        // One download at a time: a second start must not clear the first one's cancel flag or share its .part file.
        if (!busy.compareAndSet(false, true)) { result.error("BUSY", "a download is already running", null); return }
        cancelled = false
        Thread {
            val part = File(dest.path + ".part")
            var conn: HttpURLConnection? = null
            try {
                dest.parentFile?.mkdirs()
                conn = open(url)
                var hops = 0
                while (conn!!.responseCode in 301..308) {
                    val next = URL(URL(conn.url.toString()), conn.getHeaderField("Location") ?: throw IllegalStateException("redirect without Location")).toString()
                    conn.disconnect()
                    if (++hops > MAX_HOPS || !allowedRedirect(next)) throw IllegalStateException("redirect not allowed: $next")
                    conn = open(next)
                }
                if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")
                val total = if (size > 0) size else conn.contentLengthLong
                val md = MessageDigest.getInstance("SHA-256")
                val buf = ByteArray(BUF)
                var n = 0L
                var lastSent = 0L
                conn.inputStream.use { input ->
                    part.outputStream().use { out ->
                        while (true) {
                            if (cancelled) throw Cancelled()
                            val r = input.read(buf)
                            if (r < 0) break
                            md.update(buf, 0, r); out.write(buf, 0, r); n += r
                            if (n - lastSent >= PROGRESS_STEP) { lastSent = n; val d = n; main.post { progress(d, total) } }
                        }
                    }
                }
                if (size > 0 && n != size) throw IllegalStateException("size $n, expected $size")
                val hex = md.digest().joinToString("") { "%02x".format(it) }
                if (sha.isNotEmpty() && hex != sha) throw IllegalStateException("SHA-256 mismatch")
                // rename(2) on one filesystem replaces atomically and works on API 24; the old file is never deleted first.
                if (!part.renameTo(dest)) throw IllegalStateException("cannot move download into place")
                conn?.disconnect(); conn = null
                busy.set(false)
                main.post { progress(n, total); result.success(dest.path) }
            } catch (e: Exception) {
                part.delete()
                val code = if (e is Cancelled) "CANCELLED" else "DOWNLOAD_FAILED"
                conn?.disconnect(); conn = null
                busy.set(false)
                main.post { result.error(code, e.message ?: e.toString(), null) }
            } finally {
                conn?.disconnect()
                busy.set(false)
            }
        }.start()
    }

    private fun open(u: String): HttpURLConnection = (URL(u).openConnection() as HttpURLConnection).apply {
        instanceFollowRedirects = false
        connectTimeout = 15_000
        readTimeout = 30_000
    }

    private class Cancelled : Exception("cancelled")

    companion object {
        private const val BUF = 1 shl 18
        private const val PROGRESS_STEP = 2L shl 20
        private const val MAX_HOPS = 5
        const val RELEASE_PREFIX = "https://github.com/sunososobro-hub/tinglongwu/releases/download/"

        fun allowedStart(u: String) = u.startsWith(RELEASE_PREFIX) && !u.contains("..")

        /** GitHub serves release assets from a CDN host after redirecting; https only. */
        fun allowedRedirect(u: String): Boolean {
            val parsed = try { URL(u) } catch (e: Exception) { return false }
            if (parsed.protocol != "https" || parsed.userInfo != null) return false
            val host = parsed.host
            return host == "github.com" || host.endsWith(".githubusercontent.com")
        }
    }
}
