package com.refineview.wizzy;

import static org.junit.Assert.*;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.security.NetworkSecurityPolicy;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.util.UUID;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(AndroidJUnit4.class)
public class DeviceSecurityTest {
    private final Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();

    @Test public void keySurvivesStorageReopenWithoutPlaintext() throws Exception {
        String token = UUID.randomUUID().toString();
        DeviceStore first = new DeviceStore(context);
        first.put(token);
        SharedPreferences saved = context.getSharedPreferences("learning-device", Context.MODE_PRIVATE);
        assertFalse(saved.getAll().toString().contains(token));
        assertEquals(token, new DeviceStore(context).get());
        first.clear();
        assertEquals("", new DeviceStore(context).get());
    }

    @Test public void damagedEncryptedKeyCannotBeUsed() throws Exception {
        DeviceStore store = new DeviceStore(context);
        store.put(UUID.randomUUID().toString());
        context.getSharedPreferences("learning-device", Context.MODE_PRIVATE).edit()
            .putString("credential", "invalid-encrypted-key").commit();
        assertEquals("", store.get());
    }

    @Test public void releaseSecurityPolicyRejectsCleartextAndBackups() {
        assertFalse(NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted());
        assertEquals(0, context.getApplicationInfo().flags & ApplicationInfo.FLAG_ALLOW_BACKUP);
    }
}
