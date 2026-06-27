"""Reflection handler for pattern discovery and skill induction."""

from __future__ import annotations

from uuid import uuid4

from ontoagent.butler.event_bus import ButlerEvent
from ontoagent.butler.handlers.base import BaseHandler, HandlerContext, HandlerResult
from ontoagent.butler.skills.store import SkillEntity, SkillLayer


class ReflectionHandler(BaseHandler):
    """反思归纳 Handler — 从重复模式中沉淀技能。

    监听 handler.completed 事件，分析事件模式，将重复模式沉淀为技能规则。
    """

    handler_id = "butler.reflection"
    event_types = ["handler.completed"]

    async def handle(self, event: ButlerEvent, ctx: HandlerContext) -> HandlerResult:
        """处理 handler.completed 事件，归纳模式。

        Args:
            event: handler.completed 事件，payload 包含 original_event_type,
                   handler_id, success, file_extension, duration_ms。
            ctx: HandlerContext。

        Returns:
            HandlerResult，包含创建/更新的技能信息。
        """
        if ctx.skill_store is None:
            return HandlerResult(success=False, error="skill_store not available")

        payload = event.payload
        original_event_type = payload.get("original_event_type", "")
        file_extension = payload.get("file_extension", "unknown")

        # 生成模式签名
        signature = self._generate_signature(original_event_type, file_extension)

        # 查找已有技能
        existing_skills = await ctx.skill_store.search_by_pattern("signature", signature)

        if existing_skills:
            # 已有技能：增加命中次数，提升置信度
            skill = existing_skills[0]
            await ctx.skill_store.increment_hit_count(skill.skill_id)

            new_hit_count = skill.hit_count + 1
            new_confidence = self._calculate_confidence(new_hit_count)

            update_data: dict[str, str | float] = {"confidence": new_confidence}
            if self._should_promote_to_active(new_confidence) and skill.status != "active":
                update_data["status"] = "active"

            await ctx.skill_store.update(skill.skill_id, **update_data)

            return HandlerResult(
                success=True,
                data={
                    "action": "updated",
                    "skill_id": skill.skill_id,
                    "hit_count": new_hit_count,
                    "confidence": new_confidence,
                    "promoted": update_data.get("status") == "active",
                },
            )
        else:
            # 新模式：创建候选技能
            skill_id = f"skill-{uuid4().hex[:12]}"
            new_skill = SkillEntity(
                skill_id=skill_id,
                name=f"Pattern: {signature}",
                layer=SkillLayer.RULE,
                pattern={"signature": signature},
                action={"original_event_type": original_event_type, "handler_id": payload.get("handler_id", "")},
                confidence=0.5,
                source="reflection",
                status="candidate",
                hit_count=0,
            )

            await ctx.skill_store.create(new_skill)

            return HandlerResult(
                success=True,
                data={
                    "action": "created",
                    "skill_id": skill_id,
                    "signature": signature,
                    "confidence": 0.5,
                },
            )

    def _generate_signature(self, event_type: str, file_extension: str) -> str:
        """生成模式签名。

        Args:
            event_type: 原始事件类型。
            file_extension: 文件扩展名。

        Returns:
            模式签名字符串，如 "code.changed:.py"。
        """
        return f"{event_type}:{file_extension}"

    def _calculate_confidence(self, hit_count: int) -> float:
        """根据命中次数计算置信度。

        Args:
            hit_count: 命中次数。

        Returns:
            置信度值，范围 [0.5, 1.0]。
        """
        base_confidence = 0.5
        increment = 0.1
        max_confidence = 1.0
        return min(base_confidence + hit_count * increment, max_confidence)

    def _should_promote_to_active(self, confidence: float) -> bool:
        """判断是否应该将技能提升为 active 状态。

        Args:
            confidence: 当前置信度。

        Returns:
            是否应该提升。
        """
        return confidence >= 0.8
