# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0

"""异常定义"""


class AgentsConfigError(Exception):
    """基础异常类"""
    pass


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
