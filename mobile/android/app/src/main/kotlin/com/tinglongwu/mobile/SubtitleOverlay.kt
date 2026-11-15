package com.tinglongwu.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * Floating subtitle window above other apps (SYSTEM_ALERT_WINDOW, #998).
 * Drag the text to move it, drag the corner handle to resize the width, tap to return to the app.
 * Hidden while the app itself is in front (its own subtitle screen shows the same lines).
 * All calls on the main thread.
 */
@SuppressLint("StaticFieldLeak") // holds the application context only
object SubtitleOverlay {
    private var app: Context? = null
    private var wm: WindowManager? = null
    private var root: LinearLayout? = null
    private var text: TextView? = null
    private var params: WindowManager.LayoutParams? = null

    private var wanted = false // Dart asked for the overlay (phone-audio mode)
    private var appInFront = true
    private var fontSp = 20f
    private var bgAlpha = 0.6f
    private var lastText = ""
    private var lastHint: String? = null

    fun canDraw(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(ctx)

    fun show(ctx: Context) {
        app = ctx.applicationContext
        wanted = true
        sync()
    }

    fun hide() {
        wanted = false
        sync()
    }

    fun setAppInFront(front: Boolean) {
        appInFront = front
        sync()
    }

    fun update(t: String, hint: String?) {
        lastText = t
        lastHint = hint
        render()
    }

    fun style(sp: Double?, alpha: Double?) {
        if (sp != null) fontSp = sp.toFloat().coerceIn(12f, 48f)
        if (alpha != null) bgAlpha = alpha.toFloat().coerceIn(0f, 1f)
        applyStyle()
    }

    private fun sync() {
        val ctx = app ?: return
        val visible = wanted && !appInFront && canDraw(ctx)
        if (visible && root == null) attach(ctx)
        if (!visible && root != null) detach()
    }

    private fun dp(ctx: Context, v: Float): Int =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics).roundToInt()

    @SuppressLint("ClickableViewAccessibility", "SetTextI18n")
    private fun attach(ctx: Context) {
        val w = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val screenW = ctx.resources.displayMetrics.widthPixels
        val type = if (Build.VERSION.SDK_INT >= 26) WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE
        val p = params ?: WindowManager.LayoutParams(
            (screenW * 0.92f).roundToInt(),
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (screenW * 0.04f).roundToInt()
            y = (ctx.resources.displayMetrics.heightPixels * 0.70f).roundToInt()
        }
        params = p

        val tv = TextView(ctx).apply {
            setTextColor(Color.WHITE)
            maxLines = 2
            ellipsize = TextUtils.TruncateAt.START
            setShadowLayer(4f, 0f, 1f, Color.BLACK)
            val pad = dp(ctx, 10f)
            setPadding(pad, dp(ctx, 6f), pad, dp(ctx, 6f))
        }
        val handle = TextView(ctx).apply {
            text = "⇲"
            setTextColor(Color.argb(160, 255, 255, 255))
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            gravity = Gravity.BOTTOM
            setPadding(dp(ctx, 4f), 0, dp(ctx, 6f), dp(ctx, 2f))
        }
        val box = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            addView(tv, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(handle, LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.MATCH_PARENT))
        }

        val slop = ViewConfiguration.get(ctx).scaledTouchSlop
        // Move the window, or open the app on a tap.
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var dragging = false
        tv.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = p.x; startY = p.y; dragging = false
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = e.rawX - downX; val dy = e.rawY - downY
                    if (!dragging && (abs(dx) > slop || abs(dy) > slop)) dragging = true
                    if (dragging) {
                        p.x = startX + dx.roundToInt(); p.y = startY + dy.roundToInt()
                        w.updateViewLayout(box, p)
                    }
                }
                MotionEvent.ACTION_UP -> if (!dragging) openApp(ctx)
            }
            true
        }
        // Resize the width from the corner handle.
        var startW = 0
        handle.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_DOWN -> { downX = e.rawX; startW = box.width }
                MotionEvent.ACTION_MOVE -> {
                    p.width = (startW + (e.rawX - downX)).roundToInt().coerceIn(dp(ctx, 160f), screenW)
                    w.updateViewLayout(box, p)
                }
            }
            true
        }

        try {
            w.addView(box, p)
        } catch (e: Exception) {
            android.util.Log.e("tlwu.overlay", "addView failed", e)
            return
        }
        wm = w; root = box; text = tv
        applyStyle()
        render()
    }

    private fun detach() {
        try { wm?.removeView(root) } catch (_: Exception) {}
        root = null; text = null; wm = null
    }

    private fun applyStyle() {
        val r = root ?: return
        val ctx = app ?: return
        text?.setTextSize(TypedValue.COMPLEX_UNIT_SP, fontSp)
        r.background = GradientDrawable().apply {
            cornerRadius = dp(ctx, 10f).toFloat()
            setColor(Color.argb((bgAlpha * 255).roundToInt(), 0, 0, 0))
        }
    }

    private fun render() {
        val tv = text ?: return
        val hint = lastHint
        if (hint != null) {
            tv.text = hint
            tv.setTextColor(Color.rgb(255, 200, 90))
        } else {
            tv.text = lastText.ifEmpty { "…" }
            tv.setTextColor(Color.WHITE)
        }
    }

    private fun openApp(ctx: Context) {
        ctx.startActivity(Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    }
}
