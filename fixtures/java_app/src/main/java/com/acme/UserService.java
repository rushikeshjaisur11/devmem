package com.acme;

/** Loads users. */
public class UserService {
    /** Loads one user, retrying. */
    public String load(String id) {
        Retry.run(3, () -> fetch(id));
        return id;
    }

    private void fetch(String id) {}
}
