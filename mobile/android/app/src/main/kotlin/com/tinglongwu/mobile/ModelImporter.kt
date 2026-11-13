package com.tinglongwu.mobile

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.StatFs
import android.provider.OpenableColumns
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.BufferedInputStream
import java.io.File
import java.io.FilterInputStream
import java.io.InputStream
import java.security.MessageDigest
import java.util.zip.ZipInputStream

/**
 * Copies model files the user picks with the system file picker (ACTION_OPEN_DOCUMENT) into a
 * staging dir, streaming and hashing on the way. Needs no storage and no INTERNET permission.
 * Which names are accepted and whether size/SHA-256 match is decided in Dart (model_manifest.dart);
 * this side only copies the names Dart asks for, to the target paths Dart gives.
 */
class ModelImporter(private val activity: Activity) {
    private val main = Handler(Looper.getMainLooper())
    private var pending: MethodChannel.Result? = null
    @Volatile private var cancelled = false

    fun pick(result: MethodChannel.Result) {
        if (pending != null) { result.error("BUSY", "picker already open", null); return }
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*"
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        pending = result
        try {
            activity.startActivityForResult(i, REQ_PICK)
        } catch (e: Exception) {
            pending = null
            result.error("NO_PICKER", e.toString(), null)
        }
    }

    /** Returns true if this was the picker result. */
    fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?): Boolean {
        if (requestCode != REQ_PICK) return false
        val r = pending ?: return true
        pending = null
        if (resultCode != Activity.RESULT_OK || data == null) { r.success(emptyList<Any>()); return true }
        val uris = mutableListOf<Uri>()
        val clip = data.clipData
        if (clip != null) for (k in 0 until clip.itemCount) uris.add(clip.getItemAt(k).uri)
        else data.data?.let { uris.add(it) }
        r.success(uris.map { describe(it) })
        return true
    }

    private fun describe(uri: Uri): Map<String, Any?> {
        var name: String? = null
        var size: Long = -1
        try {
            activity.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    val ni = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                    val si = c.getColumnIndex(OpenableColumns.SIZE)
                    if (ni >= 0) name = c.getString(ni)
                    if (si >= 0 && !c.isNull(si)) size = c.getLong(si)
                }
            }
        } catch (_: Exception) {}
        return mapOf("uri" to uri.toString(), "name" to (name ?: uri.lastPathSegment ?: "?"), "size" to size)
    }

    fun cancel() { cancelled = true }

    fun freeBytes(dir: String): Long = try {
        File(dir).mkdirs(); StatFs(dir).availableBytes
    } catch (_: Exception) { -1L }

    /**
     * Args: uri, stagingDir, and either `target` (a single file) or `zipTargets` (basename -> target path).
     * Returns a list of {target, size, sha256} for every file copied.
     */
    fun import(call: MethodCall, result: MethodChannel.Result, progress: (Long, Long) -> Unit) {
        val uri = Uri.parse(call.argument<String>("uri"))
        val staging = File(call.argument<String>("stagingDir")!!)
        val target = call.argument<String>("target")
        val zipTargets = call.argument<Map<String, String>>("zipTargets")
        val total = (call.argument<Number>("size") ?: -1).toLong()
        cancelled = false
        Thread {
            try {
                val out = mutableListOf<Map<String, Any>>()
                val raw = activity.contentResolver.openInputStream(uri) ?: throw IllegalStateException("cannot open $uri")
                var lastSent = 0L
                val counting = Counting(BufferedInputStream(raw, BUF)) { n ->
                    if (n - lastSent >= PROGRESS_STEP) { lastSent = n; main.post { progress(n, total) } }
                }
                counting.use { input ->
                    if (zipTargets != null) {
                        val z = ZipInputStream(input)
                        while (true) {
                            val e = z.nextEntry ?: break
                            if (e.isDirectory) continue
                            val base = e.name.replace('\\', '/').substringAfterLast('/')
                            val t = zipTargets[base] ?: continue
                            out.add(copy(z, staging, t))
                        }
                    } else {
                        out.add(copy(input, staging, target!!))
                    }
                }
                main.post { progress(counting.count, total); result.success(out) }
            } catch (e: Exception) {
                val code = if (e is Cancelled) "CANCELLED" else "IMPORT_FAILED"
                main.post { result.error(code, e.message ?: e.toString(), null) }
            }
        }.start()
    }

    private fun copy(input: InputStream, staging: File, target: String): Map<String, Any> {
        require(!target.startsWith("/") && !target.split('/').contains("..")) { "unsafe target $target" }
        val f = File(staging, target)
        f.parentFile?.mkdirs()
        val md = MessageDigest.getInstance("SHA-256")
        val buf = ByteArray(BUF)
        var n = 0L
        f.outputStream().use { o ->
            while (true) {
                if (cancelled) throw Cancelled()
                val r = input.read(buf)
                if (r < 0) break
                md.update(buf, 0, r); o.write(buf, 0, r); n += r
            }
        }
        val hex = md.digest().joinToString("") { "%02x".format(it) }
        return mapOf("target" to target, "size" to n, "sha256" to hex)
    }

    private class Cancelled : Exception("cancelled")

    private class Counting(inp: InputStream, val onCount: (Long) -> Unit) : FilterInputStream(inp) {
        var count = 0L
        override fun read(): Int { val b = super.read(); if (b >= 0) { count++; onCount(count) }; return b }
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            val r = super.read(b, off, len); if (r > 0) { count += r; onCount(count) }; return r
        }
    }

    companion object {
        private const val REQ_PICK = 7301
        private const val BUF = 1 shl 20
        private const val PROGRESS_STEP = 4L shl 20
    }
}
