package com.oculus.os;

/** No-op stand-in for the Quest system telemetry class used by Meta XR Audio. */
public class AnalyticsEvent {
    public AnalyticsEvent(String name) {}
    public AnalyticsEvent setExtra(String key, Object value) { return this; }
    public void setCount(int count) {}
}
