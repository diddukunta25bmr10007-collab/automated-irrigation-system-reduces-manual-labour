package com.irry.smartirrigation;

import android.app.Activity;
import android.os.Bundle;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.view.Gravity;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private EditText urlBox;
    private WebView web;
    private SharedPreferences prefs;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("irry", MODE_PRIVATE);
        showUI();
    }

    private void showUI() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(14,14,14,14);
        root.setBackgroundColor(Color.rgb(6,16,13));

        TextView title = new TextView(this);
        title.setText("IRRY – Smart Irrigation");
        title.setTextColor(Color.WHITE);
        title.setTextSize(20);
        title.setGravity(Gravity.CENTER_VERTICAL);
        root.addView(title, new LinearLayout.LayoutParams(-1,60));

        LinearLayout conn = new LinearLayout(this);
        conn.setOrientation(LinearLayout.HORIZONTAL);

        urlBox = new EditText(this);
        urlBox.setSingleLine(true);
        urlBox.setHint("http://PC-IP:5000/mobile/");
        urlBox.setText(prefs.getString("server", "http://172.25.235.94:5000/mobile/"));
        urlBox.setTextColor(Color.WHITE);
        urlBox.setHintTextColor(Color.GRAY);
        conn.addView(urlBox, new LinearLayout.LayoutParams(0,60,1));

        Button connect = new Button(this);
        connect.setText("CONNECT");
        conn.addView(connect, new LinearLayout.LayoutParams(140,60));
        root.addView(conn);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        web.setWebViewClient(new WebViewClient());
        root.addView(web, new LinearLayout.LayoutParams(-1,0,1));

        connect.setOnClickListener(v -> connect());
        setContentView(root);
        connect();
    }

    private void connect() {
        String u = urlBox.getText().toString().trim();
        if (!u.endsWith("/")) u += "/";
        prefs.edit().putString("server", u).apply();
        web.loadUrl(u);
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
