# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""异常定义"""


class AgentsConfigError(Exception):
    """基础异常类"""
    pass


class MigrationError(AgentsConfigError):
    """配置库无法安全升级，原始数据保持不变。"""


class CatalogError(AgentsConfigError):
    """能力目录操作失败。"""


class CatalogImportError(CatalogError):
    """导入快照下载、校验或应用失败。"""


class CatalogReadOnlyError(CatalogError):
    """来源原件只读，应创建派生或本地副本。"""


class CatalogConflictError(CatalogError):
    """预览后的数据或引用发生变化。"""


class MigrationRequiredError(MigrationError):
    """旧库必须先 prepare/apply 显式升级。"""


class CapabilityNotFoundError(CatalogError):
    """能力档案未找到。"""


class CapabilityInUseError(CatalogError):
    """能力档案正被绑定引用。"""


class CapabilityTypeMismatchError(CatalogError):
    """绑定与能力档案的类型不一致。"""


class ProviderNotFoundError(AgentsConfigError):
    """Provider 未找到"""
    pass


class ProviderAlreadyExistsError(AgentsConfigError):
    """Provider 已存在"""
    pass


class ModelNotFoundError(AgentsConfigError):
    """Model Instance 未找到"""
    pass


class ModelAlreadyExistsError(AgentsConfigError):
    """Model Instance 已存在"""
    pass


class AgentNotFoundError(AgentsConfigError):
    """Agent 未找到"""
    pass


class AgentAlreadyExistsError(AgentsConfigError):
    """Agent 已存在"""
    pass
