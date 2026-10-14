import { ReadOutlined, SmileOutlined } from '@ant-design/icons';
import { FloatButton } from 'antd';
import React from 'react';

const docsUrl = process.env.NEXT_PUBLIC_DOCS_URL || 'https://docs.dbgpt.cn';

const FloatHelper: React.FC = () => {
  return (
    <div className='fixed right-4 md:right-6 bottom-[240px] md:bottom-[220px] z-[997]'>
      <FloatButton.Group trigger='hover' icon={<SmileOutlined />}>
        <FloatButton icon={<ReadOutlined />} href={docsUrl} target='_blank' tooltip='Documents' />
      </FloatButton.Group>
    </div>
  );
};

export default FloatHelper;
