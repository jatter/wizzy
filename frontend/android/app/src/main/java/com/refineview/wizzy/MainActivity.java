package com.refineview.wizzy;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import androidx.core.content.FileProvider;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private static final String ORIGIN = BuildConfig.SITE_ORIGIN;
    private static final int PICK_IMAGE = 40;
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private WebView web;
    private LinearLayout errorPanel;
    private TextView errorText;
    private ProgressBar progress;
    private DeviceStore store;
    private boolean passkeys;
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        store = new DeviceStore(this);
        cleanCameraCache();
        FrameLayout frame = new FrameLayout(this);
        frame.setBackgroundColor(Color.rgb(243, 247, 248));
        frame.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        web = new WebView(this);
        frame.addView(web, new FrameLayout.LayoutParams(-1, -1));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        frame.addView(progress, new FrameLayout.LayoutParams(-1, 6, Gravity.TOP));
        errorPanel = new LinearLayout(this);
        errorPanel.setOrientation(LinearLayout.VERTICAL);
        errorPanel.setGravity(Gravity.CENTER);
        errorPanel.setPadding(48, 48, 48, 48);
        errorPanel.setBackgroundColor(Color.rgb(243, 247, 248));
        errorText = new TextView(this);
        errorText.setTextSize(18);
        errorText.setGravity(Gravity.CENTER);
        errorPanel.addView(errorText);
        Button retry = new Button(this);
        retry.setText("重新连接");
        retry.setOnClickListener(view -> openLearningSpace());
        errorPanel.addView(retry);
        frame.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        errorPanel.setVisibility(View.GONE);
        setContentView(frame);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " WizzyAndroid/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_AUTHENTICATION)) {
            WebSettingsCompat.setWebAuthenticationSupport(settings, WebSettingsCompat.WEB_AUTHENTICATION_SUPPORT_FOR_APP);
            passkeys = WebSettingsCompat.getWebAuthenticationSupport(settings) == WebSettingsCompat.WEB_AUTHENTICATION_SUPPORT_FOR_APP;
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(web, "WizzyApp", Collections.singleton(ORIGIN),
                (view, message, sourceOrigin, isMainFrame, reply) -> {
                    if (!isMainFrame || !ORIGIN.equals(sourceOrigin.toString()) || message.getData() == null || message.getData().length() > 2048) return;
                    try {
                        JSONObject command = new JSONObject(message.getData());
                        int id = command.getInt("id");
                        String action = command.getString("action");
                        network.execute(() -> {
                            try {
                                if (action.equals("resumeSession")) {
                                    restoreSession(restored -> respond(reply, id, new JSONObject(Collections.singletonMap("restored", restored)), null));
                                    return;
                                }
                                JSONObject result = handle(action);
                                respond(reply, id, result, null);
                            } catch (Exception ignored) { respond(reply, id, null, "操作未完成，请检查安全连接和网络后重试。"); }
                        });
                    } catch (Exception ignored) { /* Reject malformed bridge messages without exposing native state. */ }
                });
        }
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (sameOrigin(uri) && !uri.getPath().endsWith(".apk")) return false;
                if (request.isForMainFrame()) {
                    if (request.hasGesture()) external(uri);
                    else showError("安全连接异常，请稍后重新连接。");
                }
                return true;
            }
            @Override public void onPageFinished(WebView view, String url) { CookieManager.getInstance().flush(); }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showError("暂时连接不上小知。请检查网络，确认设备日期正确后重试。");
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400) showError("小知暂时没有准备好，请稍后重新连接。");
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) { progress.setProgress(value); progress.setVisibility(value < 100 ? View.VISIBLE : View.GONE); }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams parameters) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    Intent images = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                    images.addCategory(Intent.CATEGORY_OPENABLE);
                    images.setType("image/*");
                    images.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, parameters.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                    Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                    if (camera.resolveActivity(getPackageManager()) != null) {
                        File directory = new File(getCacheDir(), "photos");
                        if (!directory.exists() && !directory.mkdirs()) throw new Exception("Camera cache unavailable");
                        File image = File.createTempFile("homework-", ".jpg", directory);
                        cameraUri = FileProvider.getUriForFile(MainActivity.this, getPackageName() + ".files", image);
                        camera.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
                        camera.setClipData(ClipData.newRawUri("homework", cameraUri));
                        camera.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
                        if (parameters.isCaptureEnabled()) { startActivityForResult(camera, PICK_IMAGE); return true; }
                    } else { camera = null; cameraUri = null; }
                    Intent chooser = Intent.createChooser(images, "选择作业图片");
                    if (camera != null) chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{camera});
                    startActivityForResult(chooser, PICK_IMAGE);
                } catch (Exception ignored) { fileCallback.onReceiveValue(null); fileCallback = null; }
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> external(Uri.parse(url)));
        openLearningSpace();
    }

    private boolean sameOrigin(Uri uri) {
        Uri origin = Uri.parse(ORIGIN);
        return "https".equals(uri.getScheme()) && origin.getHost().equals(uri.getHost()) && origin.getPort() == uri.getPort() && uri.getUserInfo() == null;
    }

    private void external(Uri uri) {
        if (!"https".equals(uri.getScheme()) || uri.getUserInfo() != null) return;
        try { startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)); }
        catch (Exception ignored) { showError("请先安装可打开安全链接的浏览器。"); }
    }

    private void openLearningSpace() {
        errorPanel.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        network.execute(() -> restoreSession(restored -> {
            if (restored) web.loadUrl(ORIGIN + "/?end=child");
            else CookieManager.getInstance().setCookie(ORIGIN, "wizzy_family_session=; Max-Age=0; Path=/api/v1; Secure; HttpOnly; SameSite=Strict",
                cleared -> web.loadUrl(ORIGIN + "/?end=child"));
        }));
    }

    private void showError(String message) {
        runOnUiThread(() -> { errorText.setText(message); errorPanel.setVisibility(View.VISIBLE); progress.setVisibility(View.GONE); });
    }

    private JSONObject handle(String action) throws Exception {
        if (action.equals("rememberDevice")) {
            ApiResponse response = api("/api/v1/auth/learning-device/register", new JSONObject());
            if (response.status != 201) throw new Exception("Device enrollment failed");
            store.put(response.data.getString("device_token"));
        } else if (action.equals("forgetDevice")) {
            String token = store.get();
            try {
                if (!token.isEmpty() && api("/api/v1/auth/learning-device/forget", new JSONObject().put("device_token", token)).status != 200) throw new Exception("Device revocation failed");
            } finally { store.clear(); }
        } else if (action.equals("checkUpdate")) {
            checkUpdate();
        } else if (!action.equals("status")) {
            throw new Exception("Unknown action");
        }
        return new JSONObject().put("remembered", !store.get().isEmpty()).put("passkeys", passkeys).put("version", BuildConfig.VERSION_NAME);
    }

    private void restoreSession(java.util.function.Consumer<Boolean> complete) {
        try {
            String token = store.get();
            if (!token.isEmpty()) {
                ApiResponse response = api("/api/v1/auth/learning-device/resume", new JSONObject().put("device_token", token));
                if (response.status == 200 && !response.cookies.isEmpty()) {
                    runOnUiThread(() -> {
                        AtomicInteger remaining = new AtomicInteger(response.cookies.size());
                        for (String cookie : response.cookies) CookieManager.getInstance().setCookie(ORIGIN, cookie, accepted -> {
                            if (remaining.decrementAndGet() == 0) { CookieManager.getInstance().flush(); complete.accept(true); }
                        });
                    });
                    return;
                }
                if (response.status == 401) store.clear();
            }
        } catch (Exception ignored) { /* Keep the encrypted key on a transient network failure. */ }
        runOnUiThread(() -> complete.accept(false));
    }

    private void respond(androidx.webkit.JavaScriptReplyProxy reply, int id, JSONObject data, String error) {
        runOnUiThread(() -> {
            try { reply.postMessage(new JSONObject().put("id", id).put("ok", error == null).put("data", data).put("error", error).toString()); }
            catch (Exception ignored) { /* The page may have closed while native work was pending. */ }
        });
    }

    private ApiResponse api(String path, JSONObject body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(ORIGIN + path).openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
        connection.setRequestMethod("POST"); connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setRequestProperty("Origin", ORIGIN);
        connection.setRequestProperty("X-Family-Session", "browser");
        String cookies = CookieManager.getInstance().getCookie(ORIGIN + "/api/v1");
        if (cookies != null) connection.setRequestProperty("Cookie", cookies);
        try {
            try (java.io.OutputStream output = connection.getOutputStream()) { output.write(body.toString().getBytes(StandardCharsets.UTF_8)); }
            int status = connection.getResponseCode();
            JSONObject data = new JSONObject();
            InputStream input = status < 400 ? connection.getInputStream() : connection.getErrorStream();
            if (input != null) try (InputStream stream = input) { data = new JSONObject(readJson(stream)).optJSONObject("data"); }
            List<String> received = new ArrayList<>();
            for (Map.Entry<String, List<String>> header : connection.getHeaderFields().entrySet()) {
                if ("Set-Cookie".equalsIgnoreCase(header.getKey())) received.addAll(header.getValue());
            }
            return new ApiResponse(status, data == null ? new JSONObject() : data, received);
        } finally { connection.disconnect(); }
    }

    private void checkUpdate() throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(ORIGIN + "/downloads/android/latest.json").openConnection();
        connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(10000);
        connection.setRequestProperty("Cache-Control", "no-cache");
        try (InputStream input = connection.getInputStream()) {
            JSONObject release = new JSONObject(readJson(input));
            int version = release.getInt("version_code");
            String path = release.getString("url");
            if (!path.matches("/downloads/android/[a-zA-Z0-9._-]+\\.apk")) throw new Exception("Invalid release");
            runOnUiThread(() -> {
                AlertDialog.Builder dialog = new AlertDialog.Builder(this).setTitle("小知 APP 更新");
                if (version > BuildConfig.VERSION_CODE) dialog.setMessage("有新版本可下载，请由家长完成安装。").setPositiveButton("下载更新", (d, which) -> external(Uri.parse(ORIGIN + path))).setNegativeButton("稍后", null);
                else dialog.setMessage("当前已是最新版本 " + BuildConfig.VERSION_NAME).setPositiveButton("知道了", null);
                dialog.show();
            });
        } finally { connection.disconnect(); }
    }

    @Override protected void onActivityResult(int request, int result, Intent intent) {
        super.onActivityResult(request, result, intent);
        if (request != PICK_IMAGE || fileCallback == null) return;
        Uri[] files = null;
        if (result == RESULT_OK) {
            files = WebChromeClient.FileChooserParams.parseResult(result, intent);
            if (files == null && cameraUri != null) files = new Uri[]{cameraUri};
        }
        fileCallback.onReceiveValue(files); fileCallback = null; cameraUri = null;
    }

    private String readJson(InputStream input) throws Exception {
        java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > 65536) throw new Exception("Response is too large");
            output.write(buffer, 0, count);
        }
        return output.toString(StandardCharsets.UTF_8.name());
    }

    private void cleanCameraCache() {
        File[] images = new File(getCacheDir(), "photos").listFiles();
        if (images != null) for (File image : images) if (image.lastModified() < System.currentTimeMillis() - 86400000L) image.delete();
    }

    @Override public void onBackPressed() { if (web.canGoBack()) web.goBack(); else super.onBackPressed(); }
    @Override protected void onStop() { CookieManager.getInstance().flush(); super.onStop(); }
    @Override protected void onDestroy() {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        web.destroy(); network.shutdown(); super.onDestroy();
    }

    private static final class ApiResponse {
        final int status;
        final JSONObject data;
        final List<String> cookies;
        ApiResponse(int status, JSONObject data, List<String> cookies) { this.status = status; this.data = data; this.cookies = cookies; }
    }
}
