package com.example.annotation;

/**
 * 标记需要认证的方法。
 */
public @interface RequiresAuth {
    String role() default "user";
}
