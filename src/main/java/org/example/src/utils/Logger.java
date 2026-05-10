package org.example.src.utils;

public class Logger {
    public enum LogLevel {
        DEBUG(1),
        INFO(2),
        WARN(3),
        ERROR(4),
        OFF(5);

        private final int value;

        LogLevel(int value) {
            this.value = value;
        }

        public boolean shouldLog(LogLevel other) {
            return this.value <= other.value;
        }
    }

    private static LogLevel currentLevel = LogLevel.INFO; // Default to INFO to hide DEBUG logs

    public static boolean detailedStorage = false;

    public static void setLevel(LogLevel level) {
        currentLevel = level;
    }

    public static void debug(String message) {
        if (LogLevel.DEBUG.value >= currentLevel.value) {
            System.out.println("[DEBUG] " + message);
        }
    }

    public static void info(String message) {
        if (LogLevel.INFO.value >= currentLevel.value) {
            System.out.println("[INFO] " + message);
            CostLogger.log("[INFO] " + message);
        }
    }

    public static void warn(String message) {
        if (LogLevel.WARN.value >= currentLevel.value) {
            System.out.println("[WARN] " + message);
        }
    }

    public static void error(String message) {
        if (LogLevel.ERROR.value >= currentLevel.value) {
            System.err.println("[ERROR] " + message);
        }
    }
}
