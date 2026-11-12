#version 450
vec2 points[3] = vec2[3](
    vec2(-0.85, -0.85), vec2(0.85, -0.85), vec2(0.0, 0.85));
void main() {
    gl_Position = vec4(points[gl_VertexIndex], 0.0, 1.0);
}
