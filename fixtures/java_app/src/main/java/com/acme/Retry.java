package com.acme;

/** Retry helpers. */
public final class Retry {
    /** Runs the task up to n times. */
    public static void run(int n, Runnable task) {
        for (int i = 0; i < n; i++) { task.run(); }
    }
}
