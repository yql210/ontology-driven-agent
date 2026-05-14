package com.example.repository;

import com.example.model.User;

/**
 * 用户数据访问接口。
 */
public interface UserRepository {

    /**
     * 根据ID查询用户。
     * @param id 用户ID
     * @return 用户对象，不存在返回null
     */
    User query(long id);

    /**
     * 保存用户。
     * @param user 用户对象
     */
    void save(User user);

    /**
     * 删除用户。
     * @param id 用户ID
     */
    void delete(long id);
}
