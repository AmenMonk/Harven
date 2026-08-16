# Copyright 2026 AmenMonk. Licensed under the Apache License, Version 2.0.
"""
Agent 管理面板 — 注册/编辑/删除/测试 Agent
"""
from flask import request, jsonify
from src.core.agent_registry import get_registry

def register_agent_routes(app):
    
    @app.route('/api/agents', methods=['GET'])
    def agent_list():
        try:
            return jsonify({'agents': get_registry().list_agents()})
        except Exception as e:
            return jsonify({'agents': [], 'error': str(e)})
    
    @app.route('/api/agents/register', methods=['POST'])
    def agent_register():
        """注册新 Agent"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            required = ['id', 'name', 'role', 'system_prompt']
            for key in required:
                if not data.get(key):
                    return jsonify({'success': False, 'error': f'缺少必填字段: {key}'}), 400
            
            registry = get_registry()
            ok = registry.register(
                agent_id=data['id'],
                name=data['name'],
                role=data['role'],
                system_prompt=data['system_prompt'],
                model_pref=data.get('model_pref', 'local'),
                skills=data.get('skills', []),
                triggers=data.get('triggers', [])
            )
            return jsonify({'success': ok, 'error': '' if ok else 'Agent ID 已被内置 Agent 占用'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/agents/<agent_id>', methods=['DELETE'])
    def agent_delete(agent_id):
        """删除自定义 Agent"""
        try:
            registry = get_registry()
            agent = registry._agents.get(agent_id)
            if not agent:
                return jsonify({'success': False, 'error': 'Agent 不存在'})
            if not agent.get('custom'):
                return jsonify({'success': False, 'error': '不能删除内置 Agent'})
            del registry._agents[agent_id]
            registry._save()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/agents/<agent_id>', methods=['PUT'])
    def agent_update(agent_id):
        """更新 Agent 配置"""
        try:
            data = request.get_json(force=True, silent=True) or {}
            registry = get_registry()
            agent = registry._agents.get(agent_id)
            if not agent:
                return jsonify({'success': False, 'error': 'Agent 不存在'})
            
            for key in ['name', 'role', 'system_prompt', 'model_pref', 'triggers']:
                if key in data:
                    agent[key] = data[key]
            if 'skills' in data:
                agent['skills'] = data['skills']
            
            registry._save()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
