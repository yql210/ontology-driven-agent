package com.example.model;

/**
 * 用户实体类。
 */
public class User {

    private String name;
    private String email;
    private long id;

    public User(String name, String email) {
        this.name = name;
        this.email = email;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getEmail() {
        return email;
    }

    public long getId() {
        return id;
    }
}
