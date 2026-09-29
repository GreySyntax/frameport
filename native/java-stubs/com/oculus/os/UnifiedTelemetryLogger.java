package com.oculus.os;

import android.content.Context;

/** No-op stand-in for the Quest system telemetry logger used by Meta XR Audio. */
public class UnifiedTelemetryLogger {
    private static final UnifiedTelemetryLogger INSTANCE = new UnifiedTelemetryLogger();
    public static UnifiedTelemetryLogger getInstance(Context context) { return INSTANCE; }
    public void reportEvent(AnalyticsEvent event, boolean immediate) {}
}
