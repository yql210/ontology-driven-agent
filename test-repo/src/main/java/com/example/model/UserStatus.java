package com.example.model;

/**
 * 用户状态枚举。
 */
public enum UserStatus implements Comparable<UserStatus> {
    ACTIVE,
    INACTIVE,
    SUSPENDED;

    public String getLabel() {
        return name().toLowerCase();
    }
}
