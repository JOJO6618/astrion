package com.cyjai.agent

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.DocumentsContract
import android.provider.Settings
import android.util.Log
import android.view.ViewGroup
import android.view.View
import android.content.Context
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLConnection
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var voiceBridge: VoiceBridge? = null
    private var pendingFileChooserParams: WebChromeClient.FileChooserParams? = null

    companion object {
        private const val TAG = "MainActivity"
        private const val REQUEST_RECORD_AUDIO = 300
        private const val REQUEST_WRITE_STORAGE = 301
        private const val REQUEST_READ_MEDIA = 302
        private val HOME_URL: String = BuildConfig.HOME_URL
        private val HOME_HOST: String? = Uri.parse(HOME_URL).host
        private const val WEB_ASSET_VERSION = "20260624_2"
    }

    private val fileChooserLauncher: ActivityResultLauncher<Intent> = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val results = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        filePathCallback?.onReceiveValue(results)
        filePathCallback = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        webView.layoutParams = ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        )
        webView.isVerticalScrollBarEnabled = false
        webView.isHorizontalScrollBarEnabled = false
        webView.overScrollMode = View.OVER_SCROLL_NEVER
        setContentView(webView)

        ViewCompat.setOnApplyWindowInsetsListener(webView) { view, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(0, 0, 0, systemBars.bottom)
            insets
        }
        ViewCompat.requestApplyInsets(webView)

        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            mediaPlaybackRequiresUserGesture = false
            // 你的 Nginx 对静态资源设置了 immutable 长缓存，这里对 App 侧禁用缓存，确保样式更新及时生效
            cacheMode = WebSettings.LOAD_NO_CACHE
            useWideViewPort = true
            loadWithOverviewMode = true
            setSupportZoom(false)
            builtInZoomControls = false
            displayZoomControls = false
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                safeBrowsingEnabled = true
            }
        }
        webView.clearCache(true)

        webView.addJavascriptInterface(ThemeBridge(), "AndroidThemeBridge")
        webView.addJavascriptInterface(PdfPreviewBridge(), "AndroidPdfBridge")
        webView.addJavascriptInterface(DownloadBridge(), "AndroidDownloadBridge")

        // 语音识别桥接（立即注册，前端始终可检测到；引擎在首次使用时懒初始化）
        voiceBridge = VoiceBridge(this@MainActivity, webView)
        webView.addJavascriptInterface(voiceBridge!!, "AndroidVoiceBridge")

        // 后台检查并下载模型
        lifecycleScope.launch(Dispatchers.IO) {
            ModelManager.init(this@MainActivity)
            if (!ModelManager.isModelReady(this@MainActivity)) {
                Log.i(TAG, "首次启动，开始下载语音模型...")
                withContext(Dispatchers.Main) {
                    injectVoiceStatus("downloading")
                }
                val success = ModelManager.downloadModels(this@MainActivity) { pct, msg ->
                    Log.i(TAG, "模型下载: ${pct}% - $msg")
                    runOnUiThread {
                        injectVoiceDownloadProgress(pct, msg)
                    }
                }
                if (!success) {
                    Log.e(TAG, "模型下载失败")
                    withContext(Dispatchers.Main) {
                        injectVoiceStatus("error")
                    }
                    return@launch
                }
            }
            Log.i(TAG, "模型就绪")
            withContext(Dispatchers.Main) {
                injectVoiceStatus("ready")
                // 模型就绪后立即预初始化识别引擎，用户点击时直接可用
                voiceBridge?.ensureEngine()
            }
        }

        // 请求录音权限
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                REQUEST_RECORD_AUDIO
            )
        }

        // Android 9 及以下需要写外部存储权限才能使用 DownloadManager
        if (Build.VERSION.SDK_INT <= Build.VERSION_CODES.P &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE),
                REQUEST_WRITE_STORAGE
            )
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                injectThemeObserver()
                injectMobileOverlayWidthPatch()
                injectNoPageScrollPatch()
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url?.toString() ?: return false
                val uri = Uri.parse(url)
                // APK 下载优先：虽然是站内 host，也要交给系统下载器/浏览器
                if (isApkUrl(url)) {
                    startActivity(Intent(Intent.ACTION_VIEW, uri))
                    return true
                }
                // 站内页面：WebView 内加载。用 host 精确比较，防止 example.com.evil.com 之类伪造前缀
                if ((uri.scheme == "http" || uri.scheme == "https") && uri.host == HOME_HOST) {
                    return false
                }
                // 外部链接：跳系统浏览器（或对应 app，如 mailto/tel）打开；
                // 无 handler 的自定义 scheme（如 download://）直接吞掉，维持原行为
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, uri))
                    true
                } catch (e: ActivityNotFoundException) {
                    true
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                // 按需放行媒体权限（例如文件上传触发的媒体访问）
                request.grant(request.resources)
            }

            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                this@MainActivity.filePathCallback?.onReceiveValue(null)
                this@MainActivity.filePathCallback = filePathCallback

                // 对图片/视频选择，先检查并请求媒体权限；部分国产 ROM 的文件管理器会要求
                // 应用持有 READ_MEDIA_IMAGES/READ_MEDIA_VIDEO 才能展示本地媒体。
                val acceptTypes = fileChooserParams?.acceptTypes?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }
                    ?: listOf("*/*")
                if (needsMediaPermission(acceptTypes) && !hasMediaPermission(acceptTypes)) {
                    pendingFileChooserParams = fileChooserParams
                    requestMediaPermissions(acceptTypes)
                    return true
                }

                launchFileChooser(fileChooserParams)
                return true
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    finish()
                }
            }
        })

        if (savedInstanceState == null) {
            webView.loadUrl(buildHomeUrl(), mapOf(
                "Cache-Control" to "no-cache, no-store, must-revalidate",
                "Pragma" to "no-cache"
            ))
        } else {
            webView.restoreState(savedInstanceState)
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        webView.saveState(outState)
        super.onSaveInstanceState(outState)
    }

    override fun onDestroy() {
        voiceBridge?.destroy()
        webView.destroy()
        super.onDestroy()
    }

    private fun needsMediaPermission(acceptTypes: List<String>): Boolean {
        // 仅当选择器明确针对图片或视频时才需要媒体权限；通配类型仍走 DocumentsUI 的临时授权
        if (acceptTypes.isEmpty() || acceptTypes.any { it == "*/*" }) return false
        return acceptTypes.any { it.startsWith("image/") || it.startsWith("video/") }
    }

    private fun getRequiredMediaPermissions(acceptTypes: List<String>): Array<String> {
        val needsImage = acceptTypes.any { it.startsWith("image/") }
        val needsVideo = acceptTypes.any { it.startsWith("video/") }
        return when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU -> {
                val perms = mutableListOf<String>()
                if (needsImage) perms.add(Manifest.permission.READ_MEDIA_IMAGES)
                if (needsVideo) perms.add(Manifest.permission.READ_MEDIA_VIDEO)
                perms.toTypedArray()
            }
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M -> {
                arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
            }
            else -> emptyArray()
        }
    }

    private fun hasMediaPermission(acceptTypes: List<String>): Boolean {
        val perms = getRequiredMediaPermissions(acceptTypes)
        if (perms.isEmpty()) return true
        return perms.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun requestMediaPermissions(acceptTypes: List<String>) {
        val perms = getRequiredMediaPermissions(acceptTypes)
        if (perms.isEmpty()) return
        ActivityCompat.requestPermissions(this, perms, REQUEST_READ_MEDIA)
    }

    private fun launchFileChooser(fileChooserParams: WebChromeClient.FileChooserParams?) {
        pendingFileChooserParams = null
        val acceptTypes = fileChooserParams?.acceptTypes?.filter { it.isNotBlank() }?.takeIf { it.isNotEmpty() }
            ?: listOf("*/*")
        val allowMultiple = fileChooserParams?.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = acceptTypes.first()
            if (allowMultiple) {
                putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            }
            if (acceptTypes.size > 1) {
                putExtra(Intent.EXTRA_MIME_TYPES, acceptTypes.toTypedArray())
            }
        }
        fileChooserLauncher.launch(intent)
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            REQUEST_READ_MEDIA -> {
                val params = pendingFileChooserParams
                if (params != null) {
                    if (grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                        launchFileChooser(params)
                    } else {
                        filePathCallback?.onReceiveValue(null)
                        filePathCallback = null
                        pendingFileChooserParams = null
                        val permanentlyDenied = grantResults.isNotEmpty() &&
                                permissions.isNotEmpty() &&
                                !ActivityCompat.shouldShowRequestPermissionRationale(this, permissions[0])
                        if (permanentlyDenied) {
                            Toast.makeText(this, "请在系统设置中开启媒体访问权限", Toast.LENGTH_LONG).show()
                            openAppSettings()
                        } else {
                            Toast.makeText(this, "需要媒体访问权限才能选择本地文件", Toast.LENGTH_LONG).show()
                        }
                    }
                }
            }
            REQUEST_WRITE_STORAGE, REQUEST_RECORD_AUDIO -> {
                // 启动时请求的权限，无需额外处理
            }
        }
    }

    private fun openAppSettings() {
        try {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.fromParts("package", packageName, null)
            }
            startActivity(intent)
        } catch (_: Exception) {
            // ignore
        }
    }

    private fun applySystemBarTheme(rawTheme: String?) {
        val theme = (rawTheme ?: "").lowercase(Locale.getDefault())
        val isDark = theme == "dark"
        val bgColor = if (isDark) Color.BLACK else Color.WHITE

        window.statusBarColor = bgColor
        window.navigationBarColor = bgColor
        WindowCompat.getInsetsController(window, window.decorView)?.let { controller ->
            controller.isAppearanceLightStatusBars = !isDark
            controller.isAppearanceLightNavigationBars = !isDark
        }
    }

    private fun injectThemeObserver() {
        val script = """
            (function() {
              function readTheme() {
                var t = document.documentElement.getAttribute('data-theme')
                    || document.body.getAttribute('data-theme')
                    || localStorage.getItem('agents_ui_theme')
                    || 'claude';
                return String(t).toLowerCase();
              }
              function notify() {
                if (window.AndroidThemeBridge && window.AndroidThemeBridge.onThemeChanged) {
                  window.AndroidThemeBridge.onThemeChanged(readTheme());
                }
              }
              notify();
              if (!window.__androidThemeObserverInstalled) {
                window.__androidThemeObserverInstalled = true;
                var ob = new MutationObserver(function() { notify(); });
                ob.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
                if (document.body) {
                  ob.observe(document.body, { attributes: true, attributeFilter: ['data-theme'] });
                }
                window.addEventListener('storage', function(e){
                  if (e && e.key === 'agents_ui_theme') notify();
                });
              }
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    private fun injectMobileOverlayWidthPatch() {
        val script = """
            (function() {
              var styleId = '__android_mobile_overlay_width_patch';
              var css = [
                '.mobile-panel-sheet.mobile-panel-sheet--conversation {',
                '  width: min(var(--conversation-expanded-width, 306px), 100vw) !important;',
                '  max-width: 100vw !important;',
                '  padding-top: 0 !important;',
                '}',
                '.mobile-panel-sheet--conversation .mobile-overlay-content {',
                '  height: 100% !important;',
                '}',
                '.mobile-panel-sheet.mobile-panel-sheet--workspace {',
                '  width: fit-content !important;',
                '  min-width: min(420px, 60vw) !important;',
                '  max-width: 100vw !important;',
                '}'
              ].join('\n');
              var existing = document.getElementById(styleId);
              if (existing) {
                existing.textContent = css;
                return;
              }
              var style = document.createElement('style');
              style.id = styleId;
              style.type = 'text/css';
              style.textContent = css;
              (document.head || document.documentElement).appendChild(style);
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    private fun injectNoPageScrollPatch() {
        val script = """
            (function() {
              var styleId = '__android_no_page_scroll_patch';
              var css = [
                'html, body {',
                '  overflow: hidden !important;',
                '  height: 100% !important;',
                '  max-height: 100% !important;',
                '}',
                'body {',
                '  position: fixed !important;',
                '  width: 100% !important;',
                '  inset: 0 !important;',
                '}',
                '* {',
                '  scrollbar-width: none !important;',
                '}',
                '*::-webkit-scrollbar {',
                '  width: 0 !important;',
                '  height: 0 !important;',
                '  display: none !important;',
                '}',
                '#app, .app-root, .chat-container {',
                '  max-height: 100% !important;',
                '  overflow: hidden !important;',
                '}'
              ].join('\n');
              var existing = document.getElementById(styleId);
              if (existing) {
                existing.textContent = css;
                return;
              }
              var style = document.createElement('style');
              style.id = styleId;
              style.type = 'text/css';
              style.textContent = css;
              (document.head || document.documentElement).appendChild(style);
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    // ═══════════════════ 语音状态通知（JS 注入） ═══════════════════

    private fun injectVoiceStatus(status: String) {
        val script = """
            (function() {
                if (window.__onVoiceStatus) window.__onVoiceStatus('$status');
                if (window.dispatchEvent) {
                    window.dispatchEvent(new CustomEvent('voicebridge:status', { detail: '$status' }));
                }
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    private fun injectVoiceDownloadProgress(pct: Int, msg: String) {
        val safeMsg = msg.replace("'", "\\'")
        val script = """
            (function() {
                if (window.__onVoiceDownloadProgress) {
                    window.__onVoiceDownloadProgress($pct, '$safeMsg');
                }
            })();
        """.trimIndent()
        webView.evaluateJavascript(script, null)
    }

    inner class PdfPreviewBridge {
        @JavascriptInterface
        fun previewPdf(pdfUrl: String?) {
            val url = pdfUrl ?: return
            runOnUiThread {
                val intent = Intent(this@MainActivity, PdfPreviewActivity::class.java)
                intent.putExtra(PdfPreviewActivity.EXTRA_PDF_URL, url)
                startActivity(intent)
            }
        }

        @JavascriptInterface
        fun isPdfPreviewSupported(): Boolean = true
    }

    inner class DownloadBridge {
        @JavascriptInterface
        fun downloadFile(fileUrl: String?, fileName: String?) {
            val url = fileUrl ?: return
            val name = fileName?.takeIf { it.isNotBlank() }
                ?: deriveFileName("", null, url)
            runOnUiThread {
                startSystemDownload(url, name)
            }
        }
    }

    private fun startSystemDownload(rawUrl: String, fileName: String) {
        // 不再使用系统 DownloadManager（国产 ROM 上 enqueue 后经常静默失败）。
        // 改为应用内下载到私有目录，然后通过系统分享 sheet 让用户选择保存位置。
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val absoluteUrl = resolveAbsoluteUrl(rawUrl)
                if (absoluteUrl.isBlank()) {
                    withContext(Dispatchers.Main) {
                        Toast.makeText(this@MainActivity, "下载链接无效", Toast.LENGTH_LONG).show()
                    }
                    return@launch
                }

                withContext(Dispatchers.Main) {
                    Toast.makeText(this@MainActivity, "正在下载：$fileName", Toast.LENGTH_SHORT).show()
                }

                // CookieManager 必须在主线程访问
                val cookie = withContext(Dispatchers.Main) {
                    CookieManager.getInstance().getCookie(absoluteUrl)
                }

                val downloadedFile = downloadFileToAppDir(absoluteUrl, fileName, cookie)

                withContext(Dispatchers.Main) {
                    shareDownloadedFile(downloadedFile)
                }
            } catch (e: Exception) {
                Log.e(TAG, "下载失败: ${e.message}", e)
                withContext(Dispatchers.Main) {
                    Toast.makeText(this@MainActivity, "下载失败：${e.message}", Toast.LENGTH_LONG).show()
                    // 兜底：尝试用浏览器打开
                    try {
                        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(resolveAbsoluteUrl(rawUrl)))
                        startActivity(intent)
                    } catch (_: Exception) {}
                }
            }
        }
    }

    private fun downloadFileToAppDir(urlString: String, preferredName: String, cookie: String?): File {
        val dir = File(filesDir, "downloads").apply { mkdirs() }
        val connection = URL(urlString).openConnection() as HttpURLConnection
        connection.connectTimeout = 30000
        connection.readTimeout = 30000
        connection.instanceFollowRedirects = true
        connection.setRequestProperty("Cookie", cookie ?: "")
        connection.connect()

        val finalUrl = connection.url.toString()
        val contentDisposition = connection.getHeaderField("Content-Disposition")
        val fileName = deriveFileName(preferredName, contentDisposition, finalUrl)

        val file = File(dir, fileName)
        connection.inputStream.use { input ->
            file.outputStream().use { output ->
                input.copyTo(output)
            }
        }
        connection.disconnect()
        return file
    }

    private fun shareDownloadedFile(file: File) {
        try {
            val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
            val mimeType = URLConnection.guessContentTypeFromName(file.name) ?: "application/octet-stream"
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = mimeType
                putExtra(Intent.EXTRA_STREAM, uri)
                putExtra(Intent.EXTRA_TITLE, file.name)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            val chooser = Intent.createChooser(intent, "分享文件").apply {
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(chooser)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "没有可用的文件分享应用", Toast.LENGTH_LONG).show()
        } catch (e: Exception) {
            Log.e(TAG, "分享文件失败: ${e.message}", e)
            Toast.makeText(this, "分享文件失败：${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun deriveFileName(preferredName: String, contentDisposition: String?, url: String): String {
        contentDisposition?.let {
            val regex = Regex("filename\\*?=\\s*\"?([^\";]+)\"?", RegexOption.IGNORE_CASE)
            regex.find(it)?.groupValues?.get(1)?.trim()?.takeIf { name -> name.isNotBlank() }?.let { return it }
        }
        if (preferredName.isNotBlank() && preferredName.contains(".")) return preferredName
        Uri.parse(url).lastPathSegment?.takeIf { it.isNotBlank() && it.contains(".") }?.let { return it }
        Uri.parse(url).getQueryParameter("path")?.split("/")?.lastOrNull()?.takeIf { it.isNotBlank() }?.let { return it }
        return "download_${System.currentTimeMillis()}"
    }

    private fun resolveAbsoluteUrl(rawUrl: String): String {
        if (rawUrl.startsWith("http://") || rawUrl.startsWith("https://")) return rawUrl
        // API 始终位于域名根路径，不能拿当前 WebView URL（可能包含对话路径 /conv_id）作为 base，
        // 否则会把 /api/download/file 拼到对话路径后面，导致 404。
        val base = HOME_URL.trimEnd('/')
        return if (rawUrl.startsWith("/")) "$base$rawUrl" else "$base/$rawUrl"
    }

    inner class ThemeBridge {
        @JavascriptInterface
        fun onThemeChanged(theme: String?) {
            runOnUiThread {
                applySystemBarTheme(theme)
            }
        }

        @JavascriptInterface
        fun getAppVersionCode(): String {
            return getInstalledVersionCode().toString()
        }

        @JavascriptInterface
        fun getAppVersionName(): String {
            return getInstalledVersionName()
        }
    }

    private fun buildHomeUrl(): String {
        val vc = getInstalledVersionCode()
        val vn = Uri.encode(getInstalledVersionName())
        return "$HOME_URL/?app_shell=$WEB_ASSET_VERSION&app_vc=$vc&app_vn=$vn"
    }

    private fun getInstalledVersionCode(): Long {
        val pkgInfo = packageManager.getPackageInfo(packageName, 0)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) pkgInfo.longVersionCode else @Suppress("DEPRECATION") pkgInfo.versionCode.toLong()
    }

    private fun getInstalledVersionName(): String {
        val pkgInfo = packageManager.getPackageInfo(packageName, 0)
        return pkgInfo.versionName ?: "unknown"
    }

    private fun isApkUrl(url: String): Boolean {
        return url.lowercase(Locale.getDefault()).endsWith(".apk") || url.contains("/api/app/apk/latest")
    }
}
