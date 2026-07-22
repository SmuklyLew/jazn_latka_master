package pl.smuklylew.asterlauncher;

import android.app.Activity;
import android.app.role.RoleManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.BaseAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextClock;
import android.widget.TextView;
import android.widget.Toast;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

public final class MainActivity extends Activity {
    private final List<AppEntry> allApps = new ArrayList<>();
    private final List<AppEntry> shownApps = new ArrayList<>();

    private AppAdapter adapter;
    private TextView appCount;
    private EditText search;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildInterface();
        loadApplications();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (adapter != null && !allApps.isEmpty()) {
            filterApplications(search == null ? "" : search.getText().toString());
        }
    }

    private void buildInterface() {
        final int margin = dp(16);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(margin, dp(12), margin, dp(8));

        GradientDrawable background = new GradientDrawable(
                GradientDrawable.Orientation.TL_BR,
                new int[]{0xD9141822, 0xC9222838, 0xE00D1017});
        root.setBackground(background);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);

        LinearLayout clockPanel = new LinearLayout(this);
        clockPanel.setOrientation(LinearLayout.VERTICAL);

        TextClock clock = new TextClock(this);
        clock.setFormat12Hour("HH:mm");
        clock.setFormat24Hour("HH:mm");
        clock.setTextSize(38);
        clock.setTextColor(Color.WHITE);

        TextClock date = new TextClock(this);
        date.setFormat12Hour("EEEE, d MMMM");
        date.setFormat24Hour("EEEE, d MMMM");
        date.setTextSize(14);
        date.setTextColor(0xFFCAD2E3);

        clockPanel.addView(clock);
        clockPanel.addView(date);
        header.addView(clockPanel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));

        Button defaultButton = new Button(this);
        defaultButton.setText("Ustaw HOME");
        defaultButton.setAllCaps(false);
        defaultButton.setOnClickListener(v -> requestDefaultLauncher());
        header.addView(defaultButton, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(48)));

        root.addView(header, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = new TextView(this);
        title.setText("Aster Launcher");
        title.setTextSize(22);
        title.setTextColor(Color.WHITE);
        title.setPadding(0, dp(10), 0, dp(4));
        root.addView(title);

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setOrientation(LinearLayout.HORIZONTAL);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);

        search = new EditText(this);
        search.setHint("Szukaj aplikacji…");
        search.setSingleLine(true);
        search.setTextColor(Color.WHITE);
        search.setHintTextColor(0xFFABB4C7);
        search.setBackgroundColor(0x443D475B);
        search.setPadding(dp(14), 0, dp(14), 0);
        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                filterApplications(s == null ? "" : s.toString());
            }
            @Override public void afterTextChanged(Editable s) { }
        });
        searchRow.addView(search, new LinearLayout.LayoutParams(0, dp(48), 1f));

        appCount = new TextView(this);
        appCount.setGravity(Gravity.CENTER);
        appCount.setTextColor(0xFFE3E8F3);
        appCount.setTextSize(13);
        searchRow.addView(appCount, new LinearLayout.LayoutParams(dp(72), dp(48)));

        root.addView(searchRow, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        GridView grid = new GridView(this);
        grid.setNumColumns(GridView.AUTO_FIT);
        grid.setColumnWidth(dp(78));
        grid.setHorizontalSpacing(dp(6));
        grid.setVerticalSpacing(dp(10));
        grid.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        grid.setPadding(0, dp(12), 0, dp(12));
        grid.setClipToPadding(false);
        grid.setSelector(android.R.color.transparent);

        adapter = new AppAdapter(this, shownApps);
        grid.setAdapter(adapter);
        grid.setOnItemClickListener((parent, view, position, id) -> launchApplication(shownApps.get(position)));
        grid.setOnItemLongClickListener((parent, view, position, id) -> {
            openApplicationDetails(shownApps.get(position));
            return true;
        });

        root.addView(grid, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView footer = new TextView(this);
        footer.setText("Dotknij ikony, aby uruchomić • przytrzymaj, aby otworzyć informacje");
        footer.setGravity(Gravity.CENTER);
        footer.setTextColor(0xFFAEB7C9);
        footer.setTextSize(11);
        footer.setPadding(0, dp(4), 0, dp(4));
        root.addView(footer);

        setContentView(root);
    }

    private void loadApplications() {
        PackageManager packageManager = getPackageManager();
        Intent query = new Intent(Intent.ACTION_MAIN);
        query.addCategory(Intent.CATEGORY_LAUNCHER);

        List<ResolveInfo> resolved = packageManager.queryIntentActivities(query, 0);
        allApps.clear();

        for (ResolveInfo info : resolved) {
            if (info.activityInfo == null) continue;
            String packageName = info.activityInfo.packageName;
            String className = info.activityInfo.name;
            if (packageName.equals(getPackageName())) continue;

            CharSequence loadedLabel = info.loadLabel(packageManager);
            String label = loadedLabel == null ? packageName : loadedLabel.toString().trim();
            Drawable icon = info.loadIcon(packageManager);
            allApps.add(new AppEntry(label, packageName, className, icon));
        }

        Collator collator = Collator.getInstance(new Locale("pl", "PL"));
        collator.setStrength(Collator.PRIMARY);
        Collections.sort(allApps, (left, right) -> collator.compare(left.label, right.label));
        filterApplications(search == null ? "" : search.getText().toString());
    }

    private void filterApplications(String phrase) {
        if (adapter == null) return;
        String needle = phrase == null ? "" : phrase.trim().toLowerCase(Locale.ROOT);
        shownApps.clear();

        for (AppEntry app : allApps) {
            if (needle.isEmpty()
                    || app.label.toLowerCase(Locale.ROOT).contains(needle)
                    || app.packageName.toLowerCase(Locale.ROOT).contains(needle)) {
                shownApps.add(app);
            }
        }

        appCount.setText(shownApps.size() + " / " + allApps.size());
        adapter.notifyDataSetChanged();
    }

    private void launchApplication(AppEntry app) {
        Intent launch = new Intent(Intent.ACTION_MAIN);
        launch.addCategory(Intent.CATEGORY_LAUNCHER);
        launch.setComponent(new ComponentName(app.packageName, app.className));
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        try {
            startActivity(launch);
        } catch (Exception error) {
            Toast.makeText(this, "Nie udało się uruchomić: " + app.label, Toast.LENGTH_SHORT).show();
            loadApplications();
        }
    }

    private void openApplicationDetails(AppEntry app) {
        Intent details = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:" + app.packageName));
        try {
            startActivity(details);
        } catch (Exception error) {
            Toast.makeText(this, "Brak ekranu informacji o aplikacji", Toast.LENGTH_SHORT).show();
        }
    }

    private void requestDefaultLauncher() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            RoleManager roleManager = (RoleManager) getSystemService(Context.ROLE_SERVICE);
            if (roleManager != null && roleManager.isRoleAvailable(RoleManager.ROLE_HOME)) {
                if (roleManager.isRoleHeld(RoleManager.ROLE_HOME)) {
                    Toast.makeText(this, "Aster Launcher jest już domyślnym ekranem HOME", Toast.LENGTH_SHORT).show();
                    return;
                }
                startActivityForResult(roleManager.createRequestRoleIntent(RoleManager.ROLE_HOME), 1001);
                return;
            }
        }

        Intent home = new Intent(Intent.ACTION_MAIN);
        home.addCategory(Intent.CATEGORY_HOME);
        try {
            startActivity(Intent.createChooser(home, "Wybierz domyślny launcher"));
        } catch (Exception error) {
            Toast.makeText(this, "Otwórz Ustawienia → Aplikacje domyślne → Ekran główny", Toast.LENGTH_LONG).show();
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static final class AppEntry {
        final String label;
        final String packageName;
        final String className;
        final Drawable icon;

        AppEntry(String label, String packageName, String className, Drawable icon) {
            this.label = label;
            this.packageName = packageName;
            this.className = className;
            this.icon = icon;
        }
    }

    private final class AppAdapter extends BaseAdapter {
        private final Context context;
        private final List<AppEntry> entries;

        AppAdapter(Context context, List<AppEntry> entries) {
            this.context = context;
            this.entries = entries;
        }

        @Override public int getCount() { return entries.size(); }
        @Override public AppEntry getItem(int position) { return entries.get(position); }
        @Override public long getItemId(int position) { return position; }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            Holder holder;
            if (convertView == null) {
                LinearLayout cell = new LinearLayout(context);
                cell.setOrientation(LinearLayout.VERTICAL);
                cell.setGravity(Gravity.CENTER);
                cell.setPadding(dp(4), dp(6), dp(4), dp(4));
                cell.setMinimumHeight(dp(100));

                ImageView icon = new ImageView(context);
                icon.setScaleType(ImageView.ScaleType.FIT_CENTER);
                cell.addView(icon, new LinearLayout.LayoutParams(dp(54), dp(54)));

                TextView label = new TextView(context);
                label.setGravity(Gravity.TOP | Gravity.CENTER_HORIZONTAL);
                label.setTextColor(Color.WHITE);
                label.setTextSize(11);
                label.setMaxLines(2);
                label.setPadding(0, dp(5), 0, 0);
                cell.addView(label, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(38)));

                holder = new Holder(icon, label);
                cell.setTag(holder);
                convertView = cell;
            } else {
                holder = (Holder) convertView.getTag();
            }

            AppEntry entry = getItem(position);
            holder.icon.setImageDrawable(entry.icon);
            holder.label.setText(entry.label);
            convertView.setContentDescription(entry.label);
            return convertView;
        }
    }

    private static final class Holder {
        final ImageView icon;
        final TextView label;

        Holder(ImageView icon, TextView label) {
            this.icon = icon;
            this.label = label;
        }
    }
}
