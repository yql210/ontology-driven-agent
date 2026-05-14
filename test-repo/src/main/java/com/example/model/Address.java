package com.example.model;

/**
 * 地址记录类。
 */
public record Address(String street, String city, String zipCode) {

    public String getFullAddress() {
        return street + ", " + city + " " + zipCode;
    }
}
