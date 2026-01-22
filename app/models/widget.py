"""Widget-related Pydantic models"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class WidgetConfig(BaseModel):
    """Widget configuration response"""
    chatbot_id: str
    name: str
    theme: Dict[str, Any]
    first_message: Optional[str] = None
    placeholder_text: Optional[str] = None
    forms: List[Dict[str, Any]] = []
    faqs: List[Dict[str, Any]] = []
    
    # Position and colors
    widget_position: str = "bottom-right"
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    
    # Widget button styling
    widget_button_color: Optional[str] = None
    widget_text_color: Optional[str] = None
    widget_size: Optional[str] = None
    widget_border_radius: Optional[str] = None
    widget_button_text: Optional[str] = None
    
    # Overlay/appearance
    widget_overlay_color: Optional[str] = None
    widget_overlay_opacity: Optional[str] = None
    widget_fullscreen: Optional[str] = None
    widget_custom_css: Optional[str] = None
    hide_branding: bool = False
    
    # Gradient support
    theme_color_type: Optional[str] = None
    theme_gradient_start: Optional[str] = None
    theme_gradient_end: Optional[str] = None
    theme_gradient_angle: Optional[int] = None


class WidgetMessageRequest(BaseModel):
    """Widget message request (public, no auth)"""
    visitor_id: str
    message: str
    conversation_id: Optional[str] = None


class WidgetMessageResponse(BaseModel):
    """Widget message response"""
    conversation_id: str
    message: str
    actions: Optional[List[Dict[str, Any]]] = None
