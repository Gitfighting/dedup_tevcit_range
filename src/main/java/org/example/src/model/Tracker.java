package org.example.src.model;

import org.example.src.utils.Definitions;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 统计追踪器，等效于 Python 的 Tracker。
 */
public class Tracker {
    public final Map<String, List<Double>> latencyData = new HashMap<>();
    public final Map<String, List<Double>> tputRuntimeData = new HashMap<>();
    public final Map<String, List<Double>> throughputData = new HashMap<>();
    public final List<Double> transferCosts = new ArrayList<>();
    public final List<java.math.BigDecimal> storageCosts = new ArrayList<>();
    public final List<java.math.BigDecimal> storageCostsWithoutBase = new ArrayList<>();
    public final List<Double> requestCosts = new ArrayList<>();
    public final List<java.math.BigDecimal> requestSizes = new ArrayList<>();
    public double totalCost = 0;

    public int numReq = 0;
    public int roundDigit = 9;
    public String duration = "0";
    public int daysToIgnore = 0;

    public Tracker() {
        latencyData.put("read", new ArrayList<>());
        latencyData.put("write", new ArrayList<>());
        tputRuntimeData.put("read", new ArrayList<>());
        tputRuntimeData.put("write", new ArrayList<>());
        throughputData.put("read", new ArrayList<>());
        throughputData.put("write", new ArrayList<>());
    }

    public void setDuration(String duration) {
        this.duration = duration;
    }

    public void addThroughput(String operation, double throughput) {
        throughputData.get(operation).add(throughput);
    }

    public void setDayToIgnore(int daysToIgnore) {
        this.daysToIgnore = daysToIgnore;
    }

    public void addTputRuntime(String operation, double tputRuntime) {
        tputRuntimeData.get(operation).add(tputRuntime);
    }

    public void addLatency(String operation, double latency) {
        latencyData.get(operation).add(latency);
    }

    public void addTransferCost(double cost) {
        transferCosts.add(cost);
    }

    public void addStorageCost(java.math.BigDecimal cost) {
        storageCosts.add(cost);
    }

    public void addStorageCostWithoutBase(java.math.BigDecimal cost) {
        storageCostsWithoutBase.add(cost);
    }

    public void addRequestCost(double cost) {
        requestCosts.add(cost);
    }

    public void addRequestSize(java.math.BigDecimal size) {
        requestSizes.add(size);
        numReq += 1;
        // Debug: Log every 100,000th request
        // if (numReq % 10000000 == 0) {
        // org.example.src.utils.Logger
        // .debug("Added request " + numReq + ", size: " + size + ", list size: " +
        // requestSizes.size());
        // }
    }

    private double computeAverage(List<Double> data) {
        return data.isEmpty() ? 0 : round(sum(data) / data.size(), 4);
    }

    private double sum(List<Double> data) {
        double s = 0;
        // console logging removed for brevity
        for (double v : data)
            s += v;
        return s;
    }

    private java.math.BigDecimal sumBigDecimal(List<java.math.BigDecimal> data) {
        return data.stream().reduce(java.math.BigDecimal.ZERO, java.math.BigDecimal::add);
    }

    private double round(double v, int digits) {
        double scale = Math.pow(10, digits);
        return Math.round(v * scale) / scale;
    }

    public Map<String, Object> getMetrics() {
        double totTransfer = round(sum(transferCosts), roundDigit);
        java.math.BigDecimal totStorageBD = sumBigDecimal(storageCosts);
        double totStorage = totStorageBD.setScale(roundDigit, java.math.RoundingMode.HALF_UP).doubleValue();
        java.math.BigDecimal totStorageNoBaseBD = sumBigDecimal(storageCostsWithoutBase);
        double totStorageNoBase = totStorageNoBaseBD.setScale(roundDigit, java.math.RoundingMode.HALF_UP).doubleValue();
        double totRequest = round(sum(requestCosts), roundDigit);
        totalCost = totTransfer + totStorage + totRequest;

        Map<String, Object> m = new HashMap<>();
        m.put("Trace Duration (s)", duration);
        m.put("Cost Calculated Since Day", daysToIgnore);
        m.put("total requests", numReq);
        java.math.BigDecimal sumSizes = sumBigDecimal(requestSizes);
        double avgSizeGB = requestSizes.isEmpty() ? 0
                : sumSizes.divide(java.math.BigDecimal.valueOf(requestSizes.size()), 20, java.math.RoundingMode.HALF_UP)
                        .divide(java.math.BigDecimal.valueOf(Definitions.GB), 20, java.math.RoundingMode.HALF_UP)
                        .doubleValue();
        m.put("avg request size (GB)", avgSizeGB);
        m.put("aggregate size (GB)",
                round(sumSizes.divide(java.math.BigDecimal.valueOf(Definitions.GB), 20, java.math.RoundingMode.HALF_UP)
                        .doubleValue(), roundDigit));
        m.put("avg read latency (ms)", computeAverage(latencyData.get("read")));
        m.put("avg write latency (ms)", computeAverage(latencyData.get("write")));
        m.put("avg read tput runtime (ms)", computeAverage(tputRuntimeData.get("read")) * 1000);
        m.put("avg write tput runtime (ms)", computeAverage(tputRuntimeData.get("write")) * 1000);
        m.put("avg read throughput (Gbps)", computeAverage(throughputData.get("read")));
        m.put("avg write throughput (Gbps)", computeAverage(throughputData.get("write")));
        m.put("total transfer cost ($)", totTransfer);
        m.put("total storage cost ($)", totStorage);
        m.put("total storage cost without base ($)", totStorageNoBase);
        m.put("total request cost ($)", totRequest);
        m.put("total cost without request ($)", round(totTransfer + totStorage, roundDigit));
        m.put("total cost ($)", round(totalCost, roundDigit));
        return m;
    }

    public Map<String, Object> getDetailedMetrics() {
        Map<String, Object> m = new HashMap<>();
        m.put("read_latencies", latencyData.get("read"));
        m.put("write_latencies", latencyData.get("write"));
        m.put("transfer_costs", transferCosts);
        m.put("storage_costs", storageCosts);
        m.put("storage_costs_without_base", storageCostsWithoutBase);
        m.put("request_costs", requestCosts);
        m.put("total_cost", round(totalCost, roundDigit));
        return m;
    }
}
