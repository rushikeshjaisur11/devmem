package com.acme;

import org.junit.jupiter.api.Test;

class UserServiceTest {
    @Test
    void loadsUser() {
        new UserService().load("1");
    }
}
