package com.example.service;

import com.example.model.User;
import com.example.repository.UserRepository;
import com.example.exception.UserNotFoundException;

/**
 * 用户服务类，处理用户相关的业务逻辑。
 * @author LayerKG
 * @version 1.0
 */
public class UserService {

    private UserRepository userRepository;

    /**
     * 根据ID查找用户。
     * @param id 用户ID
     * @return 用户对象
     * @throws UserNotFoundException 用户不存在时抛出
     */
    public User findById(long id) throws UserNotFoundException {
        User user = userRepository.query(id);
        if (user == null) {
            throw new UserNotFoundException("User not found: " + id);
        }
        return user;
    }

    /**
     * 创建新用户。
     * @param name 用户名
     * @param email 邮箱
     * @return 创建的用户
     */
    public User createUser(String name, String email) {
        User user = new User(name, email);
        userRepository.save(user);
        return user;
    }

    /**
     * 删除用户。
     * @param id 用户ID
     */
    public void deleteUser(long id) {
        userRepository.delete(id);
    }
}
