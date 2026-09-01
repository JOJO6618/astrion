package com.cyjai.agent

import android.net.Uri
import android.os.Bundle
import android.view.MenuItem
import android.view.View
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.github.barteksc.pdfviewer.PDFView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

class PdfPreviewActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_PDF_URL = "pdf_url"
        private val HOME_URL: String = BuildConfig.HOME_URL
    }

    private lateinit var pdfView: PDFView
    private lateinit var loadingContainer: LinearLayout
    private lateinit var progressBar: ProgressBar
    private lateinit var statusText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pdf_preview)

        supportActionBar?.apply {
            setDisplayHomeAsUpEnabled(true)
            title = "PDF 预览"
        }

        pdfView = findViewById(R.id.pdfView)
        loadingContainer = findViewById(R.id.loadingContainer)
        progressBar = findViewById(R.id.progressBar)
        statusText = findViewById(R.id.statusText)

        val rawUrl = intent.getStringExtra(EXTRA_PDF_URL) ?: ""
        if (rawUrl.isBlank()) {
            showError("PDF 地址为空")
            return
        }

        val url = if (rawUrl.startsWith("http://") || rawUrl.startsWith("https://")) {
            rawUrl
        } else {
            HOME_URL.trimEnd('/') + (if (rawUrl.startsWith("/")) rawUrl else "/$rawUrl")
        }

        loadPdf(url)
    }

    private fun showLoading(msg: String) {
        loadingContainer.visibility = View.VISIBLE
        progressBar.visibility = View.VISIBLE
        statusText.visibility = View.VISIBLE
        statusText.text = msg
        pdfView.visibility = View.INVISIBLE
    }

    private fun hideLoading() {
        loadingContainer.visibility = View.GONE
        progressBar.visibility = View.GONE
        statusText.visibility = View.GONE
        pdfView.visibility = View.VISIBLE
    }

    private fun showError(msg: String) {
        loadingContainer.visibility = View.VISIBLE
        progressBar.visibility = View.GONE
        statusText.visibility = View.VISIBLE
        statusText.text = msg
        pdfView.visibility = View.INVISIBLE
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
    }

    private fun loadPdf(url: String) {
        showLoading("加载中...")
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val file = downloadToCache(url)
                withContext(Dispatchers.Main) {
                    showPdf(file)
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    showError("PDF 加载失败: ${e.message}")
                }
            }
        }
    }

    private fun downloadToCache(url: String): File {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 30_000
        conn.readTimeout = 60_000
        conn.setRequestProperty("Accept", "application/pdf,*/*")
        conn.connect()

        if (conn.responseCode !in 200..299) {
            throw RuntimeException("HTTP ${conn.responseCode}")
        }

        val pathParam = Uri.parse(url).getQueryParameter("path")
        val fileName = if (!pathParam.isNullOrBlank()) {
            "preview_" + pathParam.replace("/", "_")
        } else {
            "preview_${System.currentTimeMillis()}.pdf"
        }
        val file = File(cacheDir, fileName)

        FileOutputStream(file).use { out ->
            conn.inputStream.use { input ->
                input.copyTo(out)
            }
        }
        conn.disconnect()
        return file
    }

    private fun showPdf(file: File) {
        pdfView.fromFile(file)
            .enableSwipe(true)
            .swipeHorizontal(false)
            .enableDoubletap(true)
            .defaultPage(0)
            .onError { showError("PDF 渲染失败") }
            .onLoad { hideLoading() }
            .load()
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return if (item.itemId == android.R.id.home) {
            finish()
            true
        } else {
            super.onOptionsItemSelected(item)
        }
    }
}
