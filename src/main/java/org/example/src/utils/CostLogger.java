package org.example.src.utils;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

public class CostLogger {
    private static BufferedWriter writer;
    private static final String LOG_DIR = "D:\\JavaProjects\\Skystore1212\\logs";
    private static final DateTimeFormatter TIMESTAMP_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss.SSS");

    // Helper method for Java 8 compatibility (String.repeat() is Java 11+)
    private static String repeatString(String str, int count) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < count; i++) {
            sb.append(str);
        }
        return sb.toString();
    }

    public static void initialize(String tag, String trace, String config, boolean setBase, int numVms) {
        try {
            // Disable logging for T29 dataset
            if (trace != null && trace.contains("T29")) {
                System.out.println("Cost logging disabled for T29 dataset.");
                return;
            }

            // Create logs directory if it doesn't exist
            File logDir = new File(LOG_DIR);
            if (!logDir.exists()) {
                logDir.mkdirs();
            }

            // Create log file with timestamp
            String timestamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
            String logFileName = String.format("cost_calculation_%s_%s.log", tag, timestamp);
            File logFile = new File(logDir, logFileName);

            writer = new BufferedWriter(new FileWriter(logFile, true));

            // Write header with detailed configuration
            String separator = repeatString("=", 100);
            writer.write(separator);
            writer.newLine();
            writer.write("Cost Calculation Log - Started at: " + LocalDateTime.now().format(TIMESTAMP_FORMAT));
            writer.newLine();
            writer.write(separator);
            writer.newLine();
            writer.newLine();

            // Configuration details
            writer.write("CONFIGURATION:");
            writer.newLine();
            writer.write("  Tag:            " + tag);
            writer.newLine();
            writer.write("  Config File:    " + config);
            writer.newLine();
            writer.write("  Trace Dataset:  " + trace);
            writer.newLine();
            writer.write("  Mode:           " + (setBase ? "Fixed-Base (FB)" : "Moving-Base (MB)"));
            writer.newLine();
            writer.write("  Number of VMs:  " + numVms);
            writer.newLine();
            writer.newLine();
            writer.write(separator);
            writer.newLine();
            writer.newLine();
            writer.flush();

            System.out.println("Cost logging initialized: " + logFile.getAbsolutePath());
        } catch (IOException e) {
            System.err.println("Failed to initialize cost logger: " + e.getMessage());
        }
    }

    public static void logStorageRemoval(String region, String objKey, java.math.BigDecimal sizeGB,
            java.math.BigDecimal durationHours, java.math.BigDecimal cost) {
        if (writer == null)
            return;

        try {
            String timestamp = LocalDateTime.now().format(TIMESTAMP_FORMAT);
            String logLine = String.format(
                    "[%s] [REMOVE-COST] Region=%s, Obj=%s, Size=%s GB, Duration=%s hrs, Cost=$%s",
                    timestamp, region, objKey, sizeGB.toPlainString(), durationHours.toPlainString(),
                    cost.toPlainString());
            writer.write(logLine);
            writer.newLine();
            writer.flush();
        } catch (IOException e) {
            System.err.println("Failed to write storage removal log: " + e.getMessage());
        }
    }

    public static void log(String message) {
        if (writer == null)
            return;
        try {
            writer.write(message);
            writer.newLine();
            writer.flush();
        } catch (IOException e) {
            System.err.println("Failed to write to cost log: " + e.getMessage());
        }
    }

    public static void logStorageRemoving(String region, int physCount, String branch,
            int ttl, java.math.BigDecimal sizeGB, java.math.BigDecimal durationHours, java.math.BigDecimal cost) {
        if (writer == null)
            return;

        /*
         * try {
         * String timestamp = LocalDateTime.now().format(TIMESTAMP_FORMAT);
         * String logLine = String.format(
         * "[%s] [REMAIN-COST] Region=%s, PhysCount=%d, Branch=%s, TTL=%d, Size=%s GB, Duration=%s hrs, Cost=$%s"
         * ,
         * timestamp, region, physCount, branch, ttl, sizeGB.toPlainString(),
         * durationHours.toPlainString(),
         * cost.toPlainString());
         * writer.write(logLine);
         * writer.newLine();
         * writer.flush();
         * } catch (IOException e) {
         * System.err.println("Failed to write storage remaining log: " +
         * e.getMessage());
         * }
         */
    }

    public static void logNetworkCost(String operation, String objKey, String src, String dst,
            java.math.BigDecimal sizeGB,
            java.math.BigDecimal cost) {
        if (writer == null)
            return;

        /*
         * try {
         * String timestamp = LocalDateTime.now().format(TIMESTAMP_FORMAT);
         * String logLine = String.format(
         * "[%s] [NET-COST-%s] Obj=%s, Src=%s, Dst=%s, Size=%s GB, TransferCost=$%s",
         * timestamp, operation.toUpperCase(), objKey, src, dst, sizeGB.toPlainString(),
         * cost.toPlainString());
         * writer.write(logLine);
         * writer.newLine();
         * writer.flush();
         * } catch (IOException e) {
         * System.err.println("Failed to write network cost log: " + e.getMessage());
         * }
         */
    }

    public static void logError(String errorMessage, Exception exception) {
        if (writer == null)
            return;

        try {
            String separator = repeatString("=", 100);
            writer.newLine();
            writer.write(separator);
            writer.newLine();
            writer.write("ERROR OCCURRED - Simulation Failed");
            writer.newLine();
            writer.write("Time: " + LocalDateTime.now().format(TIMESTAMP_FORMAT));
            writer.newLine();
            writer.write(separator);
            writer.newLine();
            writer.newLine();
            writer.write("Error Message: " + errorMessage);
            writer.newLine();
            writer.newLine();

            if (exception != null) {
                writer.write("Exception Type: " + exception.getClass().getName());
                writer.newLine();
                writer.write("Exception Message: " + exception.getMessage());
                writer.newLine();
                writer.newLine();

                writer.write("Stack Trace:");
                writer.newLine();
                for (StackTraceElement element : exception.getStackTrace()) {
                    writer.write("    at " + element.toString());
                    writer.newLine();
                }
            }

            writer.newLine();
            writer.write(separator);
            writer.newLine();
            writer.flush();

            System.err.println("Error information written to cost log file");
        } catch (IOException e) {
            System.err.println("Failed to write error to log: " + e.getMessage());
        }
    }

    public static void close() {
        if (writer != null) {
            try {
                String separator = repeatString("=", 100);
                writer.write(separator);
                writer.newLine();
                writer.write("Cost Calculation Log - Ended at: " + LocalDateTime.now().format(TIMESTAMP_FORMAT));
                writer.newLine();
                writer.write(separator);
                writer.newLine();
                writer.close();
                System.out.println("Cost logging closed.");
            } catch (IOException e) {
                System.err.println("Failed to close cost logger: " + e.getMessage());
            }
        }
    }
}
