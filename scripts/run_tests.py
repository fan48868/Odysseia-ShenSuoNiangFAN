import unittest
import sys
import os

def run_all_tests():
    """
    自动发现并运行项目中的所有单元测试。
    """
    # 将项目根目录添加到 Python 搜索路径，以确保测试脚本能找到 src 目录
    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)
    
    print("开始运行单元测试...")
    print("="*70)
    
    # 创建一个测试加载器
    loader = unittest.TestLoader()
    
    # 自动发现所有测试文件（文件名以 'test_' 开头）
    # 我们指定测试文件所在的目录，这里是项目根目录
    suite = loader.discover(start_dir=project_root, pattern='test_*.py')
    
    # 创建一个测试运行器
    # verbosity=2 会打印出更详细的测试结果
    runner = unittest.TextTestRunner(verbosity=2)
    
    # 运行测试套件
    result = runner.run(suite)
    
    print("="*70)
    print("测试运行结束。")
    
    # 如果有测试失败或出错，脚本将以非零状态码退出
    if not result.wasSuccessful():
        print("\n注意：有部分测试未通过。")
        sys.exit(1)
    else:
        print("\n恭喜！所有测试均已通过。")
        sys.exit(0)

if __name__ == '__main__':
    run_all_tests()