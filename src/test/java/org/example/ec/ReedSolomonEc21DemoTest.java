package org.example.ec;

import com.backblaze.erasure.ReedSolomon;
import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Random;

/**
 * Demo test for EC(2+1) using Backblaze Reed-Solomon.
 *
 * Key point:
 * - data shards = 2, parity shards = 1
 * - each data shard size = logicalObjectSize / 2 (when divisible)
 * - any single-shard loss can be recovered
 */
public class ReedSolomonEc21DemoTest {

    @Test
    void ec21_canRecoverAnySingleShardLoss() {//测试纠删码是否可以恢复任意一个分片丢失
        final int dataShards = 2;//数据片
        final int parityShards = 1;//奇偶校验片
        final int totalShards = dataShards + parityShards;//总片数

        // Example logical object size: 1 MiB, so each data shard is 512 KiB.//逻辑对象大小为1MiB，所以每个数据片大小为512KiB
        final int logicalObjectSize = 1024 * 1024;  
        final int shardSize = logicalObjectSize / dataShards; //每个数据片大小

        byte[][] shards = new byte[totalShards][shardSize];//总片数，每个片大小
        byte[] original = new byte[logicalObjectSize];//原始数据
        new Random(7).nextBytes(original);//随机生成原始数据

        // Split logical bytes into 2 data shards.
        System.arraycopy(original, 0, shards[0], 0, shardSize);//将原始数据分成两个数据片
        System.arraycopy(original, shardSize, shards[1], 0, shardSize);//将原始数据分成两个数据片

        // Encode one parity shard.
        ReedSolomon rs = ReedSolomon.create(dataShards, parityShards);//创建纠删码对象
        rs.encodeParity(shards, 0, shardSize);//编码一个奇偶校验片

        // Try losing each shard once and recover.
        for (int lost = 0; lost < totalShards; lost++) {//丢失一个片，恢复一个片
            byte[][] attempt = deepCopy(shards);//深拷贝
            boolean[] shardPresent = new boolean[] {true, true, true};
            Arrays.fill(attempt[lost], (byte) 0);//将丢失的片设置为0
            shardPresent[lost] = false;//丢失一个片
            rs.decodeMissing(attempt, shardPresent, 0, shardSize);//恢复丢失的片
            byte[] recovered = new byte[logicalObjectSize];//恢复的原始数据
            System.arraycopy(attempt[0], 0, recovered, 0, shardSize);//将两个数据片合并成原始数据
            System.arraycopy(attempt[1], 0, recovered, shardSize, shardSize);//
            Assertions.assertArrayEquals(original, recovered, "Failed recovering when shard " + lost + " is missing");//断言恢复的原始数据与原始数据相等
        }
    }

    private static byte[][] deepCopy(byte[][] src) {
        byte[][] out = new byte[src.length][];
        for (int i = 0; i < src.length; i++) {
            out[i] = Arrays.copyOf(src[i], src[i].length);
        }
        return out;//
    }
}

