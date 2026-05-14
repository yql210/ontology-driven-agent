package com.example.service;

/**
 * 软删除接口。
 */
public interface SoftDeletable extends Comparable<Object> {

    /**
     * 软删除。
     */
    void softDelete();

    /**
     * 检查是否已删除。
     * @return true 如果已删除
     */
    boolean isDeleted();
}
